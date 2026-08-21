"""客服工作台的 SQLite 数据访问层。

本模块位于 FastAPI 接口与 ``customer_service.db`` 之间，集中负责数据库连接、
业务数据查询、前端展示结构转换以及仿真消息写入。API 层不直接拼写 SQL，后续即使
更换数据库或调整表结构，也可以主要在本文件中完成适配。

数据库中的表按用途分为三类：

1. 原始业务数据（只读）
   - ``conversations``：从聊天记录聚合出的会话摘要，用于左侧会话列表。
   - ``chat_messages``：经过图片会话过滤后的原始聊天消息。
   - ``orders``：订单、商品、金额和物流信息。
   - 各类工单表：补发换货、线下打款、物流、不良反应、售后退货。
   - ``ticket_overview``：统一不同工单字段的只读视图。
2. 仿真数据（可写）
   - ``simulation_messages``：客服在演示页面中新发送的消息。
   - ``emotion_feedback``：客服对情绪识别结果的人工纠正与对话快照。
   - ``simulation_tickets``：客服确认后创建的本地 Mock 工单。
3. 展示派生数据（运行时生成）
   - 情绪标签、情绪分数、推荐话术和前端字段命名均由本模块转换生成，
     不回写原始聊天记录。

核心读写原则：

- 原始聊天、订单和工单只查询，不执行更新或删除。
- 演示新增消息仅写入 ``simulation_messages``，可以单独清空。
- 所有 SQL 参数均使用占位符绑定，避免把用户输入直接拼入 SQL。
- 每个请求使用独立连接，适配 FastAPI 的并发请求模型。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


# ---------------------------------------------------------------------------
# 模块一：数据库位置与连接管理
# ---------------------------------------------------------------------------

# 从当前文件向上两级得到项目根目录，使服务从任意工作目录启动时都能定位数据库。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_ROOT / "data" / "customer_service.db"


@contextmanager
def database_connection() -> Iterator[sqlite3.Connection]:
    """创建一个请求级 SQLite 连接，并在使用完成后自动关闭。

    作用：
        FastAPI 可能同时处理多个请求。SQLite 连接不应作为全局对象跨线程复用，
        因此列表查询、详情查询和消息写入都会通过该上下文管理器获取独立连接。

    配置：
        - ``timeout=10``：数据库短暂被写事务占用时最多等待 10 秒。
        - ``row_factory=sqlite3.Row``：查询结果可以使用字段名访问，减少列顺序错误。
        - ``foreign_keys=ON``：启用 ``simulation_messages.session_id`` 的外键校验。

    Yields:
        已配置的 ``sqlite3.Connection``。离开 ``with`` 代码块后连接自动关闭。

    注意：
        本函数只负责关闭连接，不自动提交写事务。写入函数必须显式调用
        ``connection.commit()``，发生异常且未提交时 SQLite 会放弃该次修改。
    """
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# 模块二：仿真存储初始化
# ---------------------------------------------------------------------------


def initialize_database() -> None:
    """检查主数据库并初始化前端演示所需的可写表。

    该函数在 FastAPI 启动阶段执行，采用 ``CREATE ... IF NOT EXISTS``，因此重复启动
    服务不会覆盖已有仿真消息。它不会创建或修改原始业务表；原始表由
    ``scripts/import_data.py`` 负责生成。

    创建对象：
        - ``simulation_messages``：保存演示过程中新增的客服/买家消息。
        - ``idx_simulation_messages_session_created``：支持按会话、时间和自增ID
          顺序读取仿真消息。
        - ``emotion_feedback``：保存原预测、纠正标签、完整输入和模型元数据。
        - ``idx_emotion_feedback_session_created``：支持后续按会话导出审核数据。
        - ``simulation_tickets``：保存本地演示创建的工单，不修改原始工单表。

    字段约束：
        - ``session_id`` 必须对应 ``conversations`` 中的真实会话。
        - ``role`` 只允许“客服”或“买家”。
        - ``message_text`` 长度限制为 1—500 个字符。

    Raises:
        FileNotFoundError: ``data/customer_service.db`` 尚未创建时抛出，提示先运行
        数据导入脚本，而不是静默生成一个空数据库。
    """
    if not DATABASE_PATH.is_file():
        raise FileNotFoundError(f"数据库不存在：{DATABASE_PATH}")

    with database_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS simulation_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT '客服' CHECK (role IN ('客服', '买家')),
                message_text TEXT NOT NULL CHECK (length(message_text) BETWEEN 1 AND 500),
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES conversations(session_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_simulation_messages_session_created
            ON simulation_messages(session_id, created_at, id)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS emotion_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                scene TEXT NOT NULL,
                message_count INTEGER NOT NULL CHECK (message_count > 0),
                messages_snapshot TEXT NOT NULL,
                predicted_emotion TEXT NOT NULL
                    CHECK (predicted_emotion IN ('neutral', 'anxious', 'dissatisfied', 'angry')),
                corrected_emotion TEXT NOT NULL
                    CHECK (corrected_emotion IN ('neutral', 'anxious', 'dissatisfied', 'angry')),
                confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
                confidence_source TEXT NOT NULL,
                emotion_token_logprobs TEXT NOT NULL,
                trend TEXT NOT NULL,
                evidence TEXT NOT NULL,
                summary TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                attempts INTEGER NOT NULL CHECK (attempts > 0),
                feedback_note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES conversations(session_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_emotion_feedback_session_created
            ON emotion_feedback(session_id, created_at, id)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS simulation_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT UNIQUE,
                session_id TEXT NOT NULL UNIQUE,
                related_order_id TEXT,
                intent TEXT NOT NULL CHECK (
                    intent IN (
                        'reship_exchange', 'offline_payment', 'logistics_ticket',
                        'adverse_reaction', 'after_sales_return'
                    )
                ),
                ticket_category TEXT NOT NULL,
                issue_type TEXT NOT NULL CHECK (length(issue_type) BETWEEN 1 AND 300),
                status TEXT NOT NULL DEFAULT '待处理',
                assignee TEXT NOT NULL CHECK (length(assignee) BETWEEN 1 AND 100),
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES conversations(session_id)
            )
            """
        )
        # 更新查询规划器统计信息，使新索引立即参与后续查询计划选择。
        connection.execute("PRAGMA optimize")
        connection.commit()


# ---------------------------------------------------------------------------
# 模块三：前端展示辅助转换
# ---------------------------------------------------------------------------


def _avatar_from_buyer(buyer: str) -> str:
    """从脱敏买家昵称中提取一个可展示的头像字符。

    Args:
        buyer: 数据库中的脱敏昵称，例如 ``"杨c**"`` 或 ``"深**"``。

    Returns:
        第一个不是星号或空格的字符；昵称为空或只有星号时返回 ``"客"``。

    说明：
        该值只用于前端圆形头像占位，不会修改或尝试还原脱敏昵称。
    """
    for character in buyer:
        if character not in {"*", " "}:
            return character
    return "客"


def _emotion_for(scene: str, last_message: str) -> dict[str, Any]:
    """根据业务场景和最新消息生成可解释的规则型情绪结果。

    Args:
        scene: 会话主场景，如“不良反应”“物流异常”“售后退货”。
        last_message: 会话当前最后一条消息。物流场景会用“收到了”等词判断问题
            是否已经解决。

    Returns:
        可直接提供给 Vue 的字典，包含：
        - ``emotion``：中文情绪标签。
        - ``emotionLevel``：``safe``、``watch`` 或 ``risk``，用于颜色样式。
        - ``emotionScore``：0—100 的演示关注度分数。
        - ``emotionHint``：客服可读的处理建议。

    设计边界：
        这里是透明、可复现的演示规则，不是医学判断或真实机器学习模型。尤其在
        不良反应场景中只提示风险与服务动作，不输出疾病诊断。
    """
    if scene == "不良反应":
        return {
            "emotion": "焦虑 · 需要安抚",
            "emotionLevel": "risk",
            "emotionScore": 82,
            "emotionHint": "用户反馈使用后不适，属于高关注场景。建议先表达关怀，避免给出诊断性结论，并提醒必要时及时就医。",
        }
    if scene == "物流异常":
        resolved = any(word in last_message for word in ("收到了", "收到啦", "找到了"))
        return {
            "emotion": "情绪已缓和" if resolved else "不满 · 需要跟进",
            "emotionLevel": "safe" if resolved else "watch",
            "emotionScore": 28 if resolved else 66,
            "emotionHint": (
                "用户已经确认问题解决，建议简短致歉并完成收尾。"
                if resolved
                else "用户关注物流结果和处理时效，建议给出明确节点，并避免重复让用户等待。"
            ),
        }
    if scene == "售后退货":
        return {
            "emotion": "平静 · 正常咨询",
            "emotionLevel": "watch",
            "emotionScore": 44,
            "emotionHint": "用户主要关心退货条件、运费和到账时间，建议用清晰步骤说明处理流程。",
        }
    if scene in {"补发换货", "线下打款"}:
        return {
            "emotion": "着急 · 等待处理",
            "emotionLevel": "watch",
            "emotionScore": 61,
            "emotionHint": "用户正在等待明确处理结果，建议确认问题、说明下一节点并给出预计时间。",
        }
    return {
        "emotion": "平稳 · 正常咨询",
        "emotionLevel": "safe",
        "emotionScore": 32,
        "emotionHint": "当前表达相对平稳，可以直接回应核心问题，并提供必要的商品或活动信息。",
    }


def _suggestions_for(scene: str, sub_scene: str) -> list[str]:
    """根据主场景和子场景生成三条固定的演示推荐话术。

    Args:
        scene: 会话主场景，决定使用哪一组服务策略。
        sub_scene: 更具体的问题类型。未命中专用场景时会被带入通用话术。

    Returns:
        长度固定为 3 的字符串列表，前端将其展示为三个“填入输入框”候选项。

    作用：
        当前项目不依赖外部模型，本函数为右侧“推荐回复”模块提供稳定的仿真结果。
        所有文本都能从代码中追溯和人工审核；将来接入自建模型时，可在保持返回
        结构不变的前提下替换该函数实现。
    """
    if scene == "不良反应":
        return [
            "理解您的着急，我们已经为您加急登记。期间请先暂停使用；如症状持续或加重，建议及时就医。",
            "非常抱歉给您带来不适。您的情况已记录，我们会安排专员优先回访并持续跟进。",
            "我们可以协助您办理“使用不适”退货退款，具体处理进度会及时同步给您。",
        ]
    if scene == "物流异常":
        return [
            "理解您等包裹的着急心情，我们已经联系快递核查，并会在有结果后第一时间同步。",
            "这边已为您登记物流工单，我们会持续跟进，今天内给您一个明确处理节点。",
            "如果最终确认包裹丢失，我们会根据订单情况协助补发或退款，不会让您承担损失。",
        ]
    if scene == "售后退货":
        return [
            "您的退货申请已经进入处理流程，寄出后记得在订单页面填写退货物流单号。",
            "仓库签收并验收无误后，退款通常会在1—3个工作日原路到账。",
            "如果订单包含运费险，平台会在退货完成后根据规则自动发起理赔。",
        ]
    if scene == "补发换货":
        return [
            "抱歉给您添麻烦了，我们已经核实情况并为您登记处理。",
            "补发或换货工单创建后会尽快安排，物流信息更新后我们会及时同步。",
            "请您先保留商品和外包装，后续如需补充信息我们会第一时间联系您。",
        ]
    return [
        f"已经了解您关于“{sub_scene}”的咨询，我来为您核实具体信息。",
        "感谢您的耐心等待，我会把关键信息整理清楚后一次性回复您。",
        "如果还有其他使用需求或偏好，也可以一起告诉我，我会综合为您建议。",
    ]


# ---------------------------------------------------------------------------
# 模块四：左侧会话列表查询
# ---------------------------------------------------------------------------


def list_conversations(query: str = "", scene: str = "") -> list[dict[str, Any]]:
    """读取会话摘要，并转换为左侧会话列表所需的数据结构。

    Args:
        query: 可选搜索词，匹配会话ID或脱敏买家昵称。参数通过 SQL 占位符绑定。
        scene: 可选业务主场景，用于精确筛选 ``scene_major``。

    Returns:
        会话摘要列表，按原始会话最后消息时间倒序排列。每项包含会话ID、买家、
        头像字符、主/子场景、最后消息、消息数量和情绪等级。

    读取的数据：
        - ``conversations``：会话基础摘要。
        - ``chat_messages``：没有仿真回复时提供原始最后消息。
        - ``simulation_messages``：存在仿真回复时优先显示最新仿真内容。

    查询策略：
        ``COALESCE`` 让仿真消息优先于原始消息成为列表预览，同时不需要修改
        ``conversations`` 或 ``chat_messages``。搜索条件为空时不生成 ``WHERE``。
    """
    conditions: list[str] = []
    parameters: list[Any] = []

    if query:
        conditions.append("(c.session_id LIKE ? OR c.buyer_nickname LIKE ?)")
        keyword = f"%{query}%"
        parameters.extend([keyword, keyword])
    if scene:
        conditions.append("c.scene_major = ?")
        parameters.append(scene)

    # SQL 的结构片段只来自程序预定义条件；真正的用户值仍通过 parameters 绑定。
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"""
        SELECT
            c.*,
            COALESCE(
                (SELECT sm.message_text
                 FROM simulation_messages sm
                 WHERE sm.session_id = c.session_id
                 ORDER BY sm.created_at DESC, sm.id DESC LIMIT 1),
                (SELECT cm.message_text
                 FROM chat_messages cm
                 WHERE cm.session_id = c.session_id
                 ORDER BY cm.message_seq DESC LIMIT 1)
            ) AS last_message
        FROM conversations c
        {where_clause}
        ORDER BY c.last_message_at DESC, c.session_id DESC
    """

    with database_connection() as connection:
        rows = connection.execute(sql, parameters).fetchall()

    # 将数据库的 snake_case 字段转换成前端使用的 camelCase 字段，并补充展示数据。
    summaries: list[dict[str, Any]] = []
    for row in rows:
        last_message = row["last_message"] or ""
        emotion = _emotion_for(row["scene_major"] or "", last_message)
        summaries.append(
            {
                "id": row["session_id"],
                "buyer": row["buyer_nickname"] or "匿名买家",
                "avatar": _avatar_from_buyer(row["buyer_nickname"] or "客"),
                "scene": row["scene_major"] or "其他咨询",
                "subScene": row["scene_minor"] or "",
                "lastTime": (row["last_message_at"] or "")[-8:-3],
                "lastMessage": last_message,
                "messageCount": row["message_count"],
                "unread": 0,
                "emotionLevel": emotion["emotionLevel"],
            }
        )
    return summaries


# ---------------------------------------------------------------------------
# 模块五：中间聊天区与右侧业务上下文聚合
# ---------------------------------------------------------------------------


def get_conversation_detail(session_id: str) -> dict[str, Any] | None:
    """聚合一个会话的聊天、订单、工单、情绪和推荐话术。

    Args:
        session_id: 要加载的真实会话ID，例如 ``"S00009"``。

    Returns:
        会话存在时返回一个完整字典，供 ``GET /api/conversations/{session_id}``
        直接序列化为 JSON；会话不存在时返回 ``None``，由 API 层转换为 404。

    读取的数据：
        - ``conversations``：买家、场景、原始时间范围等基础信息。
        - ``chat_messages``：按 ``message_seq`` 排序的原始对话。
        - ``simulation_messages``：按创建时间追加在原始对话之后的演示消息。
        - ``orders``：该会话关联的全部订单，最新订单排在前面。
        - ``ticket_overview``：不同工单表合并后的统一视图，最新工单优先。

    返回结构：
        - ``messages``：中间聊天区的数据，仿真消息带 ``simulated=True`` 标记。
        - ``orders`` 和 ``ticket``：右侧购买与售后模块的数据。
        - 情绪字段和 ``suggestions``：由本模块规则即时生成，不存入数据库。

    设计说明：
        当前页面只展示优先级最高的第一张工单；没有工单时返回明确的占位对象，
        避免前端针对空值写额外分支。订单则保留列表结构，可展示多笔订单。
    """
    with database_connection() as connection:
        conversation = connection.execute(
            "SELECT * FROM conversations WHERE session_id = ?", (session_id,)
        ).fetchone()
        if conversation is None:
            return None

        chat_rows = connection.execute(
            """
            SELECT message_id, role, message_text, sent_at
            FROM chat_messages
            WHERE session_id = ?
            ORDER BY message_seq
            """,
            (session_id,),
        ).fetchall()
        simulation_rows = connection.execute(
            """
            SELECT id, role, message_text, created_at
            FROM simulation_messages
            WHERE session_id = ?
            ORDER BY created_at, id
            """,
            (session_id,),
        ).fetchall()
        order_rows = connection.execute(
            "SELECT * FROM orders WHERE session_id = ? ORDER BY ordered_at DESC",
            (session_id,),
        ).fetchall()
        ticket_rows = connection.execute(
            "SELECT * FROM ticket_overview WHERE session_id = ? ORDER BY created_at DESC",
            (session_id,),
        ).fetchall()
        simulation_ticket = connection.execute(
            """
            SELECT ticket_id, ticket_category, issue_type, status, created_at
            FROM simulation_tickets
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()

    # 先按原始消息序号构建历史记录，确保数据库中的对话顺序保持不变。
    messages = [
        {
            "id": row["message_id"],
            "role": row["role"],
            "text": row["message_text"] or "",
            "time": (row["sent_at"] or "")[-8:-3],
            "simulated": False,
        }
        for row in chat_rows
    ]
    # 仿真消息永远追加到原始历史之后，并明确标记，便于前端显示“仿真”标签。
    messages.extend(
        {
            "id": f"sim-{row['id']}",
            "role": row["role"],
            "text": row["message_text"],
            "time": row["created_at"][-8:-3],
            "simulated": True,
        }
        for row in simulation_rows
    )

    last_message = messages[-1]["text"] if messages else ""
    scene = conversation["scene_major"] or "其他咨询"
    sub_scene = conversation["scene_minor"] or ""
    emotion = _emotion_for(scene, last_message)
    # 原始工单创建时间晚于会话结束时间，仅作为评测 Ground truth；当前工单只取 Mock。
    reference_ticket = ticket_rows[0] if ticket_rows else None
    current_ticket = simulation_ticket

    return {
        "id": conversation["session_id"],
        "buyer": conversation["buyer_nickname"] or "匿名买家",
        "avatar": _avatar_from_buyer(conversation["buyer_nickname"] or "客"),
        "scene": scene,
        "subScene": sub_scene,
        "lastTime": (conversation["last_message_at"] or "")[-8:-3],
        "lastMessage": last_message,
        "messageCount": len(messages),
        "unread": 0,
        **emotion,
        "messages": messages,
        "orders": [
            {
                "orderId": row["order_id"],
                "productName": row["product_name"],
                "quantity": row["quantity"],
                "paidAmount": row["paid_amount"],
                "status": row["order_status"],
                "carrier": row["carrier"],
                "trackingNo": row["tracking_no"],
                "gift": row["gift"],
            }
            for row in order_rows
        ],
        "ticket": (
            {
                "ticketId": current_ticket["ticket_id"],
                "category": current_ticket["ticket_category"],
                "issue": current_ticket["issue_type"],
                "status": current_ticket["status"],
            }
            if current_ticket
            else {
                "ticketId": "暂无",
                "category": "服务",
                "issue": "当前会话暂无关联工单",
                "status": "无需处理",
            }
        ),
        "referenceTicket": (
            {
                "ticketId": reference_ticket["ticket_id"],
                "category": reference_ticket["ticket_category"],
                "issue": reference_ticket["issue_type"],
                "status": reference_ticket["status"],
                "createdAt": reference_ticket["created_at"],
            }
            if reference_ticket
            else None
        ),
        "suggestions": _suggestions_for(scene, sub_scene),
    }


# ---------------------------------------------------------------------------
# 模块六：仿真消息写入
# ---------------------------------------------------------------------------


def add_simulation_message(session_id: str, text: str, role: str = "客服") -> dict[str, Any]:
    """向独立仿真表写入一条演示消息。

    Args:
        session_id: 消息所属的真实会话ID。
        text: 消息正文。API 模型和数据库约束共同限制为 1—500 个字符。
        role: 消息角色，默认“客服”；数据库仅允许“客服”或“买家”。

    Returns:
        刚创建的前端消息对象，包括 ``sim-<自增ID>``、角色、正文、时分和
        ``simulated=True`` 标记。前端可以直接追加显示，无需重新请求整个会话。

    Writes:
        只写 ``simulation_messages``，不会更新 ``chat_messages``、``conversations``、
        ``orders`` 或任何工单表。

    Raises:
        KeyError: ``session_id`` 不存在于 ``conversations`` 时抛出，由 API 层转换
        成 404，防止产生无法关联的孤立仿真消息。
    """
    created_at = datetime.now().isoformat(sep=" ", timespec="seconds")
    with database_connection() as connection:
        # 写入前显式验证会话存在，使错误信息比单纯的外键异常更容易被 API 处理。
        exists = connection.execute(
            "SELECT 1 FROM conversations WHERE session_id = ?", (session_id,)
        ).fetchone()
        if exists is None:
            raise KeyError(session_id)
        cursor = connection.execute(
            """
            INSERT INTO simulation_messages(session_id, role, message_text, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, role, text, created_at),
        )
        connection.commit()

    return {
        "id": f"sim-{cursor.lastrowid}",
        "role": role,
        "text": text,
        "time": created_at[-8:-3],
        "simulated": True,
    }


# ---------------------------------------------------------------------------
# 模块七：本地 Mock 工单写入
# ---------------------------------------------------------------------------


class DuplicateTicketError(Exception):
    """当前会话已经存在 Mock 工单。"""


class InvalidRelatedOrderError(Exception):
    """提交的订单不属于当前会话。"""


_MOCK_TICKET_CATEGORIES = {
    "reship_exchange": "补发换货",
    "offline_payment": "线下打款",
    "logistics_ticket": "物流",
    "adverse_reaction": "不良反应",
    "after_sales_return": "售后退货",
}


def add_simulation_ticket(
    *,
    session_id: str,
    intent: str,
    issue: str,
    assignee: str,
    related_order_id: str | None,
) -> dict[str, Any]:
    """创建一张本地 Mock 工单，并返回前端可直接展示的工单对象。"""
    created_at = datetime.now().isoformat(sep=" ", timespec="seconds")
    category = _MOCK_TICKET_CATEGORIES[intent]

    with database_connection() as connection:
        # 立即事务保证“检查后写入”不会被并发请求穿透。
        connection.execute("BEGIN IMMEDIATE")
        conversation = connection.execute(
            "SELECT 1 FROM conversations WHERE session_id = ?", (session_id,)
        ).fetchone()
        if conversation is None:
            raise KeyError(session_id)

        mock_ticket = connection.execute(
            "SELECT 1 FROM simulation_tickets WHERE session_id = ?", (session_id,)
        ).fetchone()
        # 原始工单均为会话结束后的业务结果，只作为 Ground truth，不阻断本次演示建单。
        if mock_ticket is not None:
            raise DuplicateTicketError(session_id)

        if related_order_id:
            order = connection.execute(
                "SELECT 1 FROM orders WHERE session_id = ? AND order_id = ?",
                (session_id, related_order_id),
            ).fetchone()
            if order is None:
                raise InvalidRelatedOrderError(related_order_id)

        cursor = connection.execute(
            """
            INSERT INTO simulation_tickets(
                session_id, related_order_id, intent, ticket_category,
                issue_type, status, assignee, created_at
            )
            VALUES (?, ?, ?, ?, ?, '待处理', ?, ?)
            """,
            (
                session_id,
                related_order_id or None,
                intent,
                category,
                issue,
                assignee,
                created_at,
            ),
        )
        ticket_id = f"MOCK-{datetime.now():%Y%m%d}-{cursor.lastrowid:04d}"
        connection.execute(
            "UPDATE simulation_tickets SET ticket_id = ? WHERE id = ?",
            (ticket_id, cursor.lastrowid),
        )
        connection.commit()

    return {
        "ticketId": ticket_id,
        "category": category,
        "issue": issue,
        "status": "待处理",
    }


# ---------------------------------------------------------------------------
# 模块八：情绪识别反馈写入
# ---------------------------------------------------------------------------


def add_emotion_feedback(
    *,
    session_id: str,
    scene: str,
    message_count: int,
    messages: list[dict[str, Any]],
    prediction: dict[str, Any],
    corrected_emotion: str,
    feedback_note: str,
) -> dict[str, Any]:
    """保存一次客服人工纠正，保留可复现本次识别的完整快照。"""
    created_at = datetime.now().isoformat(sep=" ", timespec="seconds")
    with database_connection() as connection:
        # 反馈只能关联真实会话，避免产生无法回溯的孤立标注。
        exists = connection.execute(
            "SELECT 1 FROM conversations WHERE session_id = ?", (session_id,)
        ).fetchone()
        if exists is None:
            raise KeyError(session_id)

        cursor = connection.execute(
            """
            INSERT INTO emotion_feedback(
                session_id, scene, message_count, messages_snapshot,
                predicted_emotion, corrected_emotion, confidence, confidence_source,
                emotion_token_logprobs, trend, evidence, summary, prompt_version,
                attempts, feedback_note, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                scene,
                message_count,
                json.dumps(messages, ensure_ascii=False),
                prediction["emotion"],
                corrected_emotion,
                prediction["confidence"],
                prediction["confidence_source"],
                json.dumps(prediction["emotion_token_logprobs"], ensure_ascii=False),
                prediction["trend"],
                json.dumps(prediction["evidence"], ensure_ascii=False),
                prediction["summary"],
                prediction["prompt_version"],
                prediction["attempts"],
                feedback_note,
                created_at,
            ),
        )
        connection.commit()

    return {
        "id": cursor.lastrowid,
        "session_id": session_id,
        "predicted_emotion": prediction["emotion"],
        "corrected_emotion": corrected_emotion,
        "created_at": created_at,
    }


# ---------------------------------------------------------------------------
# 模块九：演示环境重置
# ---------------------------------------------------------------------------


def reset_simulation_data() -> dict[str, int]:
    """清空全部仿真消息与 Mock 工单，并返回各自删除数量。

    Returns:
        清空前 ``simulation_messages`` 和 ``simulation_tickets`` 的记录数，便于前端
        或日志确认重置结果。

    Writes:
        只删除两个仿真表。原始聊天、会话、订单、工单和情绪纠正反馈均不受影响，
        所以该操作可以安全地用于比赛演示前恢复初始状态。

    注意：
        当前设计是全局演示环境，因此一次重置会删除所有会话的仿真回复和 Mock
        工单；如果未来需要多人独立演示，应增加演示实例ID或用户ID，再按实例范围删除。
    """
    with database_connection() as connection:
        message_count = connection.execute(
            "SELECT COUNT(*) FROM simulation_messages"
        ).fetchone()[0]
        ticket_count = connection.execute(
            "SELECT COUNT(*) FROM simulation_tickets"
        ).fetchone()[0]
        connection.execute("DELETE FROM simulation_tickets")
        connection.execute("DELETE FROM simulation_messages")
        connection.commit()
    return {"messages": message_count, "tickets": ticket_count}
