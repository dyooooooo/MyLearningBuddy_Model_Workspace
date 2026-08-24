"""临时探针：验证「挂载目录里加一个词插件，运行中的容器立刻看见」。

这份文件放在 proposals/ 下，加载器不会执行它；验证时临时复制到 vocab_ext/ 顶层，
验完删掉。留在这里是为了下次还能一分钟内复验，而不用重新想一个例子。
"""

NAME = "hotreload_probe_word"
SCOPE = "block"
DOC = "探针词：正文里出现了两个以上美元符号（只用于验证热加载，不要被检测器引用）"
REQUIRES = ["text"]
EXAMPLES = {"positive": ["$a$ 与 $b$"], "negative": ["纯文字"]}


def evaluate(fields):
    return fields["text"].count("$") >= 2
