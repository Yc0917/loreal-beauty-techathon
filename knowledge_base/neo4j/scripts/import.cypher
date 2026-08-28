// 唯一约束既保证节点主键，也使重复执行导入保持幂等。
CREATE CONSTRAINT product_id_unique IF NOT EXISTS FOR (node:Product) REQUIRE node.product_id IS UNIQUE;
CREATE CONSTRAINT sku_id_unique IF NOT EXISTS FOR (node:SKU) REQUIRE node.sku_id IS UNIQUE;
CREATE CONSTRAINT ingredient_id_unique IF NOT EXISTS FOR (node:Ingredient) REQUIRE node.ingredient_id IS UNIQUE;
CREATE CONSTRAINT faq_id_unique IF NOT EXISTS FOR (node:FAQ) REQUIRE node.faq_id IS UNIQUE;
CREATE CONSTRAINT brand_name_unique IF NOT EXISTS FOR (node:Brand) REQUIRE node.name IS UNIQUE;
CREATE CONSTRAINT category_name_unique IF NOT EXISTS FOR (node:Category) REQUIRE node.name IS UNIQUE;
CREATE CONSTRAINT skin_type_name_unique IF NOT EXISTS FOR (node:SkinType) REQUIRE node.name IS UNIQUE;
CREATE CONSTRAINT effect_name_unique IF NOT EXISTS FOR (node:Effect) REQUIRE node.name IS UNIQUE;
CREATE CONSTRAINT source_page_id_unique IF NOT EXISTS FOR (node:SourcePage) REQUIRE node.source_id IS UNIQUE;

// 商品主数据及品牌、类目和来源页面关系。
LOAD CSV WITH HEADERS FROM 'file:///products.csv' AS row
MERGE (product:Product {product_id: row.product_id})
SET product.name = row.product_name,
    product.version_name = row.version_name,
    product.positioning = row.positioning,
    product.texture = row.texture,
    product.usage = row.usage,
    product.filing_number = row.filing_number
MERGE (brand:Brand {name: row.brand})
MERGE (category:Category {name: row.category})
MERGE (source:SourcePage {source_id: row.parent_product_id})
MERGE (product)-[:BRANDED_BY]->(brand)
MERGE (product)-[:IN_CATEGORY]->(category)
MERGE (product)-[:FROM_SOURCE]->(source);

// 将管道符分隔的肤质转换为独立实体关系。
LOAD CSV WITH HEADERS FROM 'file:///products.csv' AS row
MATCH (product:Product {product_id: row.product_id})
UNWIND split(row.suitable_skin_types, '|') AS skin_name
MERGE (skin:SkinType {name: skin_name})
MERGE (product)-[:SUITABLE_FOR]->(skin);

// 将功效概念转换为独立实体关系。
LOAD CSV WITH HEADERS FROM 'file:///products.csv' AS row
MATCH (product:Product {product_id: row.product_id})
UNWIND split(row.claimed_effects, '|') AS effect_name
MERGE (effect:Effect {name: effect_name})
MERGE (product)-[:HAS_EFFECT]->(effect);

// SKU 保留价格和库存等确定性字段，供 Cypher 精确查询。
LOAD CSV WITH HEADERS FROM 'file:///skus.csv' AS row
MATCH (product:Product {product_id: row.product_id})
MERGE (sku:SKU {sku_id: row.sku_id})
SET sku.capacity_ml = toInteger(row.capacity_ml),
    sku.style = row.style,
    sku.price_cny = toFloat(row.price_cny),
    sku.stock = toInteger(row.stock),
    sku.stock_status = row.stock_status,
    sku.promotion = row.promotion,
    sku.gift = row.gift,
    sku.barcode = row.barcode
MERGE (product)-[:HAS_SKU]->(sku);

// 成分节点保存 INCI 名称、中文名称、作用和安全提示。
LOAD CSV WITH HEADERS FROM 'file:///ingredients.csv' AS row
MERGE (ingredient:Ingredient {ingredient_id: row.ingredient_id})
SET ingredient.inci_name = row.inci_name,
    ingredient.display_name = row.display_name,
    ingredient.function = row.function,
    ingredient.risk_note = row.risk_note;

// 商品成分关系保存顺序、角色和是否为关键成分。
LOAD CSV WITH HEADERS FROM 'file:///product_ingredients.csv' AS row
MATCH (product:Product {product_id: row.product_id})
MATCH (ingredient:Ingredient {ingredient_id: row.ingredient_id})
MERGE (product)-[relation:CONTAINS]->(ingredient)
SET relation.position = toInteger(row.position),
    relation.role = row.role,
    relation.is_key = toBoolean(row.is_key_ingredient);

// FAQ 作为可追溯知识节点，并保留风险等级和路由信息。
LOAD CSV WITH HEADERS FROM 'file:///faq.csv' AS row
MATCH (product:Product {product_id: row.product_id})
MERGE (faq:FAQ {faq_id: row.faq_id})
SET faq.intent = row.intent,
    faq.question = row.question,
    faq.answer = row.answer,
    faq.risk_level = row.risk_level,
    faq.expected_route = row.expected_route
MERGE (product)-[:HAS_FAQ]->(faq);
