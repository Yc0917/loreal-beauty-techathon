# LangGraph 用户情绪识别模块

这是一个与现有 FastAPI、数据库和前端解耦的独立模块。当前只负责识别用户情绪，不生成客服回复，不执行风险判断、转人工或其他后续动作。

## 情绪类别

模块只使用四类边界相对清晰的核心情绪：

| 标签 | 含义 |
|---|---|
| `neutral` | 中性、普通咨询、客观陈述或轻微犹豫 |
| `anxious` | 焦急、担忧、恐惧或紧张 |
| `dissatisfied` | 失望、质疑、不信任或轻中度挫败 |
| `angry` | 强烈责备、对抗、辱骂或升级投诉 |

业务场景和情绪标签相互独立。例如“退款型”是业务场景，用户当时的情绪可能是中性、焦虑、不满或愤怒。

## 模块结构

```text
schemas.py     输入、模型输出和最终结果结构
prompts.py     五分类 Prompt 与标签边界
recognizer.py  模型协议、Prompt 调用和证据校验
graph.py       START → emotion_recognition → END
settings.py    环境变量读取与配置校验
providers/     OpenAI 兼容模型适配器
factory.py     从环境配置组装完整 Agent
```

## 输出结构

```json
{
  "emotion": "dissatisfied",
  "confidence": 0.7421,
  "confidence_source": "token_logprob",
  "emotion_token_logprobs": [
    {"token": "diss", "logprob": -0.12, "probability": 0.8869},
    {"token": "atisfied", "logprob": -0.1783, "probability": 0.8367}
  ],
  "trend": "worsening",
  "evidence": ["都一个星期了，你们到底处理不处理"],
  "summary": "用户因处理时间过长产生明显不满",
  "prompt_version": "emotion-v1.2.0"
}
```

`confidence` 不再是模型自行生成的分数，而是情绪标签所有 Token 的联合概率：
`exp(sum(token_logprob))`。如果模型服务没有返回 logprobs，识别会显式失败，不会回退为模型自评分数。

## 配置 OpenAI 兼容接口

复制项目根目录的 `.env.example` 为 `.env`，然后填写：

```dotenv
LLM_API_KEY=你的密钥
LLM_MODEL=服务商提供的模型名称

# OpenAI 官方接口留空；其他兼容服务填写对应的 /v1 地址。
LLM_BASE_URL=https://example.com/v1

# 默认兼容性较好；服务商明确支持 JSON Schema 时可改为 json_schema。
LLM_STRUCTURED_METHOD=function_calling
LLM_TIMEOUT_SECONDS=45
LLM_MAX_RETRIES=2
```

`.env` 已加入 `.gitignore`，不要把真实密钥写进源码或提交到版本库。

## 使用方式

```python
from emotion_agent import create_emotion_agent_from_env

agent = create_emotion_agent_from_env()
result = agent.analyze(
    messages=[
        {"role": "买家", "text": "退款怎么还没到账，我已经等一周了"},
        {"role": "客服", "text": "正在为您核实，请稍等"},
        {"role": "买家", "text": "到底什么时候能处理好？"},
    ],
    scene="退款型",
)
```

底层仍保留模型注入接口。如需自定义供应商适配器，可以直接创建
`EmotionRecognitionAgent(model=你的结构化模型)`，不需要修改 Prompt 或 LangGraph。

当前阶段不包含评测脚本，也未接入现有客服主流程。
