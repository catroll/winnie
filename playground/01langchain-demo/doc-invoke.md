# invoke

`invoke()` 最常见的格式是：

```python
response = model.invoke(input)
```

## 主要参数

```python
model.invoke(
    input,
    config=None,
    **kwargs
)
```

### 1. `input`：必填

根据 Runnable 类型不同，格式不同。

**字符串：**

```python
model.invoke("你好")
```

**单条 Message：**

```python
model.invoke(
    HumanMessage(content="你好")
)
```

**多条 Message：**

```python
model.invoke([
    SystemMessage(content="你是助手"),
    HumanMessage(content="你好"),
])
```

### 2. `config`：运行配置

```python
model.invoke(
    "你好",
    config={
        "tags": ["demo"],
        "metadata": {"user_id": "123"},
    }
)
```

用于：

- tags
- metadata
- callbacks
- configurable
- recursion_limit 等

### 3. `**kwargs`：模型参数

部分模型支持直接传递：

```python
model.invoke(
    "你好",
    temperature=0.7
)
```

但具体支持哪些参数，取决于 **Provider 和模型实现**。

**最重要的是：**

```text
invoke(input, config=None, **kwargs)
       │
       ├── input：业务输入
       ├── config：LangChain 运行配置
       └── kwargs：模型特定参数
```

你现在学习 LangChain，优先记住这个标准签名即可。

## Runable

LangChain 现在的核心设计是：**Model、Prompt、Chain、Agent 都可以看作 Runnable。**

所以它们通常都支持：

```python
invoke()
ainvoke()
stream()
astream()
batch()
```

## 为什么 Agent 的第一个参数是 dict？

关键在于：**不同 Runnable 的输入类型不同。**

### Model 的输入

```python
model.invoke("你好")
```

或者：

```python
model.invoke([
    {"role": "user", "content": "你好"}
])
```

模型主要接收的是 **消息**。

### Agent 的输入

```python
agent.invoke({
    "messages": [
        {"role": "user", "content": query}
    ]
})
```

Agent 接收的是一个 **State（状态）对象**。

可以理解为：

```text
Agent Input

{
    "messages": [...]
}
```

以后 Agent 的 State 可能还有：

```python
{
    "messages": [...],
    "user_id": "123",
    "session_id": "abc",
    "context": {},
}
```

所以 Agent 使用 `dict`，而不是直接传字符串。

## 你的代码可以理解成

```python
result = agent.invoke(
    {
        "messages": [
            {
                "role": "user",
                "content": query
            }
        ]
    },
    config={
        "callbacks": [recorder]
    }
)
```

参数结构：

```text
invoke(
    input,
    config
)
```

其中：

```text
input
│
└── Agent State
    │
    └── messages


config
│
└── LangChain Runtime Config
    │
    └── callbacks
```

## 简单对比

| 对象      | invoke 输入                 |
| --------- | --------------------------- |
| ChatModel | String / Message / Messages |
| Prompt    | Dict                        |
| Chain     | 取决于 Chain                |
| Agent     | Dict / State                |

例如：

```python
# Model
model.invoke("你好")


# Prompt
prompt.invoke({
    "name": "Woody"
})


# Agent
agent.invoke({
    "messages": [
        {"role": "user", "content": "你好"}
    ]
})
```

**一句话理解：**

> `invoke()` 是 Runnable 的统一执行接口，但不同 Runnable 定义自己的 Input Schema，因此 Agent 的输入是 `dict`，其中 `messages` 是 Agent State 的核心字段。

你现在正好可以把这个知识点加入 Demo 清单：**Runnable 统一接口与 Input/Output Schema**，这是理解 LangChain 和后续 LangGraph 的关键。
