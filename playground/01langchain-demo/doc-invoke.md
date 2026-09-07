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
