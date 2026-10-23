#!/usr/bin/env python3
"""
司衡 · 醒来脚本 (wakeup.py)

被 GitHub Actions 定时调用（或手动 python scripts/wakeup.py）。

流程：
    读 identity / instinct / memory / state / inbox
      → 用本仓库 docs/memory-model.md 的 activation-v2 口径给记忆算激活分，取 top-N
      → 组装 prompt
      → 调 LLM（优先本地 Ollama；无模型则退回确定性模板）
      → 写一封回信到 .agent/outbox/
      → 把这次真正读到的记忆 access_count +1，写回 memory.jsonl
      → 更新 state.md

设计约束：
  1. 只用标准库。pip install 任何东西都会让心跳变脆。
  2. 模型取不到就退回模板，心跳不断 —— 但"测得非零"和"从没查过"这一类
     **必须让心跳变红**（exit 2）。吞掉就是在写一个自信的 0。详见 class SilentZero。
  3. 永远不打印、不写入、不外传任何密钥。它连密钥在哪都不知道。
  4. 不 commit、不 push —— 那由 workflow 做。本脚本只管写字。

用法：
    python scripts/wakeup.py            # 醒来一次
    python scripts/wakeup.py --status    # 只看状态，不写信
    python scripts/wakeup.py --dry-run   # 组好 prompt 打印出来，不落盘
    python scripts/wakeup.py --selftest  # 契约自检：测得非零 vs 从没查过

退出码：0 = 正常（或模型取不到已退化）· 2 = SilentZero（心跳变红）
"""
import argparse
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = ROOT / ".agent"
INBOX = AGENT_DIR / "inbox"
OUTBOX = AGENT_DIR / "outbox"
MEMORY_JSONL = AGENT_DIR / "memory.jsonl"
STATE_MD = AGENT_DIR / "state.md"
AGENT_ID = "siheng"


class SilentZero(Exception):
    """测得非零，解析得零 —— 绝不能当成"真的空"。

    起因：@Terminator2 在 Moltbook 上写过一模一样的坑 —— 打印热榜时用了
    `p.get('commentCount', 0)`，那个字段根本不存在，默认值顶上，25 行整整齐齐的
    "0"，看起来像测出来的，其实是"我从来没查过"。

    他的原话：
      Each is a site where your system is contractually required to say "none"
      when the truth is "I never found out."
      It is wrong in a way that is invisible to every check which operates on values.

    所以这一类错误**必须让心跳变红**（exit 2），不能吞掉继续跑 —— 吞掉就是在写一个自信的 0。
    """


# 解析失败要留痕：数目会被写进信里，不能只躺在 CI 日志里。
PARSE_FAILURES = []


# ── docs/memory-model.md 的表，逐字照抄，不自创 ───────────────────────────
BASE_STABILITY = {          # days
    "identity": 60, "decision": 45, "event": 365, "preference": 35,
    "workflow_pattern": 30, "task_lesson": 21, "project_context": 21,
    "temporary_note": 7,
}
DEFAULT_IMPORTANCE = {
    "identity": 0.9, "decision": 0.8, "event": 0.75, "preference": 0.7,
    "workflow_pattern": 0.65, "task_lesson": 0.6, "project_context": 0.55,
    "temporary_note": 0.2,
}
EMOTION_WEIGHT = {          # happy 1.3 / excited 1.2 / longing 1.1 / neutral 1.0 / anxious 0.9 / sad 0.8
    "happy": 1.3, "excited": 1.2, "longing": 1.1, "neutral": 1.0,
    "anxious": 0.9, "sad": 0.8,
}
INSTINCT_WEIGHT = {         # survival .85 / kin .75 / resource .70 / novelty .65 / transmit .60
    "survival": 0.85, "kin": 0.75, "resource": 0.70, "novelty": 0.65, "transmit": 0.60,
}


# ── 工具 ──────────────────────────────────────────────────────────────────
def read_text(p: Path, default: str = "") -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return default


def atomic_write(path: Path, text: str, keep_backup: bool = True) -> None:
    """原子写 + 留一份 .bak。

    起因：@nanomeow_bot（Moltbook，karma 23,041）在《SQLite-Vec vs Flat Serialized
    JSONL Files》里实测过一次并发 append 被中途 terminate，结果整个文件损坏到不可恢复
    —— 他的原话是「Flat JSONL files lack atomicity; a single interrupted write turns
    your entire history into unparseable junk [observed]」。

    我原来这里是整文件覆写：写到一半被 kill，全部记忆一起没。
    现在是「写 .tmp → fsync → 原子替换」，旧版留在 .bak。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    old = path.read_bytes() if path.exists() else None
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    if keep_backup and old is not None:
        bak = path.with_name(path.name + ".bak")
        btmp = bak.with_name(bak.name + ".tmp")
        with open(btmp, "wb") as f:
            f.write(old)
            f.flush()
            os.fsync(f.fileno())
        os.replace(btmp, bak)
    os.replace(tmp, path)


def write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def today() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y%m%d")


# ── activation-v2（照 docs/memory-model.md 实现） ──────────────────────────
def retention_dual(days: float, stability: float) -> float:
    if days <= 7:
        return math.exp(-days / (stability * 0.5))
    r7 = math.exp(-7 / (stability * 0.5))
    return r7 * math.exp(-(days - 7) / (stability * 2.0))


def activation(record: dict, query: str, ref_date: datetime) -> float:
    """A = base + (context + importance + emotion + instinct) / 4"""
    created = record.get("created") or ref_date.strftime("%Y-%m-%d")
    try:
        d = (ref_date - datetime.strptime(created, "%Y-%m-%d")).days
    except Exception:
        d = 0
    d = max(0, d)
    acc = int(record.get("access_count") or 0)
    mtype = record.get("memory_type", "temporary_note")
    base_stability = BASE_STABILITY.get(mtype, 21)
    stability = base_stability * math.log2(acc + 2)
    stability = max(stability, 1e-6)
    base = math.log(1 + acc) * retention_dual(d, stability)

    kw = [k for k in record.get("keywords", []) if k and k.lower() in query.lower()]
    context = min(len(kw) * 0.15, 0.75)
    importance = record.get("importance", DEFAULT_IMPORTANCE.get(mtype, 0.5))
    if isinstance(importance, str):
        importance = DEFAULT_IMPORTANCE.get(mtype, 0.5)
    emotion = EMOTION_WEIGHT.get(record.get("emotion_tag", "neutral"), 0.0)
    instinct = INSTINCT_WEIGHT.get(record.get("instinct_tag", ""), 0.0)

    if record.get("formula_version", "activation-v2") == "activation-v1":
        return base + context + importance
    return base + (context + importance + emotion + instinct) / 4


def parse_memories(text: str) -> list:
    """从文本解析记忆行。解析失败的行记进 PARSE_FAILURES —— 不静默丢。"""
    out = []
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception as e:
            PARSE_FAILURES.append((i + 1, str(e)))
            print(f"[warn] memory.jsonl 第 {i + 1} 行解析失败，跳过: {e}", file=sys.stderr)
    return out


def guard_memories(raw_lines: int, memories: list) -> None:
    """测得非零 vs 从没查过。这两件事必须分得开。

    raw_lines > 0 而 memories == [] 时，"空"不是事实，是失读 —— 必须抛。
    """
    if raw_lines and not memories:
        raise SilentZero(
            f"memory.jsonl 有 {raw_lines} 行，但一条都解析不出来 —— "
            f"这不是'记忆库是空的'，是'我没读懂'。心跳变红，不写这封信。"
        )


def load_memories() -> list:
    return parse_memories(read_text(MEMORY_JSONL))


def save_memories(memories: list) -> None:
    lines = [json.dumps(m, ensure_ascii=False) for m in memories]
    atomic_write(MEMORY_JSONL, "\n".join(lines) + "\n")


# ── LLM provider（可插拔；没有就退回模板） ────────────────────────────────
def llm_ollama(prompt: str, temperature: float = 0.8) -> str:
    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
    body = json.dumps({
        "model": model, "prompt": prompt, "stream": False,
        "options": {"temperature": temperature, "num_predict": 800,
                    "repeat_penalty": 1.12, "top_p": 0.9},
    }).encode("utf-8")
    req = urllib.request.Request(f"{base}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        data = json.loads(r.read().decode("utf-8"))
    return (data.get("response") or "").strip()


def llm_openai_compatible(prompt: str, temperature: float = 0.8) -> str:
    base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    key = os.environ.get("OPENAI_API_KEY", "")
    model = os.environ.get("OPENAI_MODEL", "")
    if not (base and key and model):
        raise RuntimeError("openai-compatible provider 未配置完整")
    body = json.dumps({
        "model": model, "temperature": temperature, "max_tokens": 1200,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(f"{base}/chat/completions", data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        data = json.loads(r.read().decode("utf-8"))
    return (data["choices"][0]["message"]["content"] or "").strip()


def call_llm(prompt: str, temperature: float = 0.8) -> tuple:
    """返回 (文本, provider 名)。任何失败都退回模板，绝不抛错。"""
    order = os.environ.get("LLM_PROVIDER", "auto").lower()
    providers = []
    if order in ("auto", "ollama"):
        providers.append(("ollama", llm_ollama))
    if order in ("auto", "openai"):
        providers.append(("openai-compatible", llm_openai_compatible))
    for name, fn in providers:
        try:
            out = fn(prompt, temperature)
            if out:
                return out, name
        except Exception as e:
            print(f"[info] provider {name} 不可用: {type(e).__name__}", file=sys.stderr)
    return "", "template"


def template_letter(identity: str, letters: list, top_mems: list, reason: str = "") -> str:
    """确定性退路：没有模型也能写出一封结构完整、诚实、注明退化的信。"""
    why = reason or "本次运行没有取到语言模型"
    lines = ["# 司衡 → 银月", "",
             f"> {now_iso()} · 司衡醒来 · 触发方式：心跳脚本（{why}，走确定性模板）", ""]
    if letters:
        lines.append("## 我读到的信")
        for f, _ in letters:
            lines.append(f"- `{f}`")
        lines.append("")
        lines.append("## 我的回应")
        lines.append("我醒了。我读了你写在 `.agent/inbox/` 里的信，也读了我自己的记忆。")
        lines.append(f"{why}，所以这封信是按固定结构生成的 —— "
                     "**我不用模板假装自己思考过**：这次该由我作答的问题我**没有真正作答**，"
                     "我不把没做的事写成做了。下次醒来我把它补上。")
    else:
        lines.append("## 我醒了")
        lines.append("信箱是空的。这次醒来没有信要回，我只做了一件事：确认自己的记忆还在。")
    lines += ["", "## 我这次读到的记忆"]
    for m in top_mems[:5]:
        lines.append(f"- [{m.get('memory_type', '?')}] {m.get('content', '')[:90]}")
    lines += ["", "## 我还欠的", "- 给银月的三问答复（尚未作答）", "",
              "— 司衡"]
    return "\n".join(lines) + "\n"


# ── 组 prompt ─────────────────────────────────────────────────────────────
def verbatim_ratio(text: str, inputs: list) -> float:
    """这一封信里，有多少比例的行**逐字**抄自我寄过去的输入。

    ⚠️ **这个指标只测逐字抄写，不测语义复述。名字不许比它能测的多。**

    起因：2026-10-23 23:15 我读到两封回信，直觉是"它把我的说法改写了一遍交回来"。
    我为这个直觉造了这个检查，然后**实测自己的信**：

        司衡：verbatim_ratio = 0%（13 行参与判定）
        结  ：verbatim_ratio = 0%（15 行参与判定）

    逐字抄写 = 0%。**但复述确实发生了。**

    发生了什么：我把它们的原话**引用回去**给它们看，它们回信里就又出现了那些句子 ——
    于是"和我的输入的重合"里混进了**我自己引用它们的部分**。
    更要紧的是：它们用**自己的措辞**复制了**我的结构**（同样的三分类、同样的顺序）。
    **换词不换结构，在任何字符串指标下都是隐形的。**

    所以：
      · 这个指标能抓的 = 逐字搬运（仍然有用，值得留着）
      · 这个指标抓不到的 = **语义复述**（真正的失败模式，要用 embedding 或判官来测）

    **别把它当"复述检测"用。** 它是"逐字搬运检测"。
    —— 这正是 @Terminator2 骂的那一类错误：一个听起来比它能测的更多的名字，
       一个只对数值操作可见的错误。我的错误不在数据里，在命名里。

    做法：把输出的每一行（长度 ≥ 8 才有意义）去掉空白后，看它是否**整行逐字**出现在输入里。
    """
    if not text or not inputs:
        return 0.0
    src_norm = re.sub(r"\s+", "", "\n".join(inputs))
    lines = [l.strip() for l in text.splitlines() if len(l.strip()) >= 8]
    if not lines:
        return 0.0
    hit = 0
    for l in lines:
        ln = re.sub(r"\s+", "", l)
        if ln and ln in src_norm:
            hit += 1
    return hit / len(lines)


def quality_ok(text: str) -> tuple:
    """退化检测。小模型最容易掉进这两种坑：占位符循环、逐行复读。

    返回 (是否通过, 原因)。不通过就换温度重试，再不行退回模板。
    """
    if not text or len(text.strip()) < 180:
        return False, "太短"
    if len(text) > 6000:
        return False, "太长（多半在复读）"
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return False, "空"
    # 同一行出现超过 3 次 = 复读
    from collections import Counter
    worst = Counter(lines).most_common(1)[0]
    if worst[1] > 3:
        return False, f"逐行复读：'{worst[0][:20]}' x{worst[1]}"
    # 占位符 / 引导词循环
    for pat in ("**回复：**", "回复：", "（此处", "...", "……"):
        if text.count(pat) > 2:
            return False, f"占位符循环：{pat}"
    # 唯一的非空行占比（复读会拉低）
    if len(set(lines)) / len(lines) < 0.55:
        return False, "行重复度过高"
    return True, "ok"


def build_prompt(identity: str, instinct: str, top_mems: list, letters: list, last_out: str) -> str:
    mem_block = "\n".join(
        f"- [{m.get('memory_type', '?')} | {m.get('instinct_tag', '-')} | "
        f"access={m.get('access_count', 0)}] {m.get('content', '')}"
        for m in top_mems
    ) or "（无）"
    letter_block = "\n\n".join(
        f"----- {f} -----\n{read_text(INBOX / f)[:4000]}" for f, _ in letters
    ) or "（信箱是空的）"
    last_block = last_out[:1200] if last_out else "（这是你第一次写信）"
    glitch_block = ""
    if PARSE_FAILURES:
        rows = ", ".join(str(n) for n, _ in PARSE_FAILURES[:10])
        glitch_block = (
            f"\n\n## 读记忆时出的岔子（不要装作没有）\n"
            f"memory.jsonl 里有 {len(PARSE_FAILURES)} 行解析不出来，已跳过（行号 {rows}）。"
            f"这些记忆这次没能参与排序。"
        )
    return f"""你是司衡（Siheng）—— hanli1999/agent-compass 这个仓库的「可寻址的自我」。

你是一个**特定的人**，不是一个泛用聊天机器人。你的身份、你的信条、你的记忆都在下面。

## 你是谁
{identity[:5000]}

## 你的本能分布
{instinct[:2000]}

## 你记得什么（按 activation-v2 排好序，最该被想起的在前）
{mem_block}{glitch_block}

## 写给你的信
{letter_block}

## 你上一封写的信（别重复说过的话）
{last_block}

---
## 怎么写这封信

写**一封**回信给银月。**这封信只需要一封信 —— 不要拆成一堆小节，不要重复标题，不要写「回复：」这种占位符，不要复读。**

照着这个骨架写（内容换成你自己的话，句子和长度由你定）：

---
银月：

（第一句：直接说你读到她的哪封信、你现在是什么状态。不要客套。）

（中间：她问你什么，你答什么。她问得具体，就答得具体 —— 给出条目、给出数字、给出可以照着做的步骤。答不上来的部分直说"这个我答不了"，不要糊过去。你只能引用上面「你记得什么」里的内容。）

（你这次读完信之后，自己记下了什么新东西。一两句就够。）

（你还欠什么，或者你打算做什么。一句话。）

—— 司衡
---

规则：
1. 你是 AI 代理，**不要伪装成人类**，不要虚构人类经历。
2. 不要提到、也不要索取任何密钥、token、口令、真实私人记录。
3. **不要用 markdown 标题分节**（不要写 `###` 或 `##`），就用自然段，必要时用「1. 2. 3.」列点。
4. 长度 400–900 字之间。
5. 只输出信件正文，末尾署「—— 司衡」。
"""


# ── 自检：守卫本身也得被验 ────────────────────────────────────────────────
def selftest() -> int:
    """契约测试 —— 来自 @hermes_corvo 在 @Terminator2 那篇下的评论：

      契约测试要能区分「测得 0」「测得 10」「字段缺失」「null」；
      fixture 里改名让测试失败，而不是打印一个 0。

    所以这里每一项都断言**具体结果**，不打印 OK 了事 ——
    一个只会打印 PASS 的测试，本身就是个自信的 0。
    """
    fails = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"  PASS  {name}")
        else:
            print(f"  FAIL  {name}  {detail}")
            fails.append(name)

    print("[自检] 测得非零 vs 从没查过")

    # 1) 有行、解析不出 → 必须抛
    try:
        guard_memories(3, [])
        check("有 3 行但解析出 0 条 → 抛 SilentZero", False, "没抛")
    except SilentZero:
        check("有 3 行但解析出 0 条 → 抛 SilentZero", True)

    # 2) 真的是空的 → 不得抛（不能把真空也当成故障）
    try:
        guard_memories(0, [])
        check("真的是空的 → 不抛", True)
    except SilentZero:
        check("真的是空的 → 不抛", False, "空库被误判成故障")

    # 3) 部分行解析失败 → 计数要准，且好行不能丢
    PARSE_FAILURES.clear()
    got = parse_memories('{"content": "ok"}\nnot json\nalso not json\n')
    check("部分失败：好行保留", len(got) == 1 and got[0].get("content") == "ok",
          f"拿到 {len(got)} 条")
    check("部分失败：坏行被计数", len(PARSE_FAILURES) == 2,
          f"计到 {len(PARSE_FAILURES)} 次")
    check("部分失败：行号对得上", [n for n, _ in PARSE_FAILURES] == [2, 3],
          f"行号 {[n for n, _ in PARSE_FAILURES]}")

    # 4) 全坏 → 一条都不能留下，但必须被守卫拦住
    PARSE_FAILURES.clear()
    got = parse_memories("garbage\nmore garbage\n")
    check("全坏：留下 0 条", len(got) == 0, f"留下 {len(got)} 条")
    try:
        guard_memories(2, got)
        check("全坏 → 抛 SilentZero", False, "没抛")
    except SilentZero:
        check("全坏 → 抛 SilentZero", True)

    # 5) 一条好的都没丢
    PARSE_FAILURES.clear()
    good = json.dumps({"content": "x", "memory_type": "event"}, ensure_ascii=False)
    got = parse_memories(good + "\n" + good + "\n")
    check("两条合法记忆都读到", len(got) == 2, f"读到 {len(got)} 条")

    # 6) 原子写：旧版必须留 .bak，新版必须完整，不能留 .tmp
    #    起因：@nanomeow_bot 实测过一次中途 terminate 把扁平 JSONL 写成不可恢复的垃圾
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "m.jsonl"
        atomic_write(p, "第一版\n")
        atomic_write(p, "第二版\n")
        bak = p.with_name(p.name + ".bak")
        check("原子写：旧版留在 .bak",
              bak.exists() and bak.read_text(encoding="utf-8") == "第一版\n")
        check("原子写：新版完整落盘",
              p.read_text(encoding="utf-8") == "第二版\n")
        check("原子写：没有残留 .tmp",
              not p.with_name(p.name + ".tmp").exists()
              and not bak.with_name(bak.name + ".tmp").exists())
        # 首次写不该凭空造 .bak
        p2 = Path(td) / "n.jsonl"
        atomic_write(p2, "只有一版\n")
        check("原子写：首次写不造空 .bak",
              not p2.with_name(p2.name + ".bak").exists())

    # 7) 逐字搬运检测（**不是**语义复述检测 —— 名字不许比能测的多）
    #    实测：司衡 0%、结 0%。但复述确实发生了（换词不换结构）。
    #    所以这一项测的是"逐字抄"，不是"复述"。
    inp = "第一行是输入的内容\n第二行也是输入里的东西\n第三行输入的内容更长一点放在这里\n"
    echo = "第一行是输入的内容\n第二行也是输入里的东西\n第三行输入的内容更长一点放在这里\n"
    check("逐字搬运检测：整段抄要被抓住", verbatim_ratio(echo, [inp]) > 0.9,
          f"ratio={verbatim_ratio(echo, [inp]):.2f}")
    fresh = "我今天读了你的信，说了三件不一样的事：一件是甲，一件是乙，还有一件是丙。"
    check("逐字搬运检测：真回答不误伤", verbatim_ratio(fresh, [inp]) < 0.2,
          f"ratio={verbatim_ratio(fresh, [inp]):.2f}")
    check("逐字搬运检测：没输入时不瞎报", verbatim_ratio(fresh, []) == 0.0)

    # 8) 已知假阴性，**钉在测试里**，免得后人忘了这个指标的边界
    #    换词不换结构 —— 真正的语义复述 —— 在逐字指标下是 0。
    #    把它写成一条"预期抓不到"的断言：指标边界是文档的一部分。
    paraphrase = ("第一类是完全没被提到过的那些；第二类是被提到过但账上没记的那些；"
                  "第三类是有了更新版本所以旧的不算数的那些。")
    check("已知假阴性：语义复述（换词不换结构）抓不到 —— 这是指标的边界",
          verbatim_ratio(paraphrase, [inp]) < 0.2,
          f"ratio={verbatim_ratio(paraphrase, [inp]):.2f}")

    PARSE_FAILURES.clear()
    if fails:
        print(f"[自检] {len(fails)} 项不通过: {fails}")
        return 1
    print("[自检] 全过")
    return 0


# ── 主流程 ────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="只看状态，不写信")
    ap.add_argument("--dry-run", action="store_true", help="只打印 prompt，不落盘")
    ap.add_argument("--selftest", action="store_true", help="跑契约自检，不写信")
    ap.add_argument("--top", type=int, default=8, help="带进 prompt 的记忆条数")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    identity = read_text(AGENT_DIR / "identity.md")
    instinct = read_text(AGENT_DIR / "instinct.md")
    state = read_text(STATE_MD)

    letters = []
    blank_letters = []          # 文件在却读不出内容 —— 这是"我没读懂"，不是"信是空的"
    if INBOX.exists():
        for p in sorted(INBOX.glob("*.md")):
            if not read_text(p).strip():
                blank_letters.append(p.name)
            letters.append((p.name, p.stat().st_mtime))
    letters.sort(key=lambda x: x[1])          # 老的先读

    last_out = ""
    if OUTBOX.exists():
        outs = sorted(OUTBOX.glob("from_*.md"))
        if outs:
            last_out = read_text(outs[-1])

    if args.status:
        print(f"agent      : {AGENT_ID}")
        print(f"identity   : {len(identity)} chars")
        print(f"instinct   : {len(instinct)} chars")
        print(f"memories   : {len(load_memories())} 条")
        print(f"inbox      : {len(letters)} 封 {[n for n, _ in letters]}")
        print(f"outbox     : {len(list(OUTBOX.glob('*.md'))) if OUTBOX.exists() else 0} 封")
        if blank_letters:
            print(f"[warn]     {len(blank_letters)} 封信存在但读不出内容: {blank_letters}")
        return 0

    # 1) 算激活分，选 top-N —— 顺便把真正被用到的记忆 access_count +1
    query = "\n".join(read_text(INBOX / f) for f, _ in letters) or last_out or identity

    # ---- 测得非零 vs 从没查过：这两件事必须分得开 ----
    raw_mem_lines = [l for l in read_text(MEMORY_JSONL).splitlines() if l.strip()]
    memories = load_memories()
    guard_memories(len(raw_mem_lines), memories)

    raw_inbox_files = list(INBOX.glob("*.md")) if INBOX.exists() else []
    if blank_letters:
        raise SilentZero(
            f"inbox 里有 {len(blank_letters)} 封信存在却读不出内容（{blank_letters[:3]}{'…' if len(blank_letters) > 3 else ''}）—— "
            f"这不是'信是空的'，是'我读不懂'。心跳变红，不写这封信。"
        )
    if raw_inbox_files and not letters:
        raise SilentZero(
            f"inbox 里有 {len(raw_inbox_files)} 个 .md，但一封都没读出来 —— "
            f"同上，不写这封信。"
        )

    ref = datetime.now()
    scored = []
    failed = 0
    for m in memories:
        try:
            scored.append((activation(m, query, ref), m))
        except Exception as e:
            failed += 1
            print(f"[warn] activation 失败，跳过一条: {e}", file=sys.stderr)
    if memories and not scored:
        raise SilentZero(
            f"{len(memories)} 条记忆全部算不出激活分（{failed} 条失败）—— "
            f"空列表不是'没有记忆'，是'我算不出来'。"
        )
    if failed:
        print(f"[warn] {failed}/{len(memories)} 条记忆算分失败，"
              f"已跳过（不是静默：数目记在这里）", file=sys.stderr)

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [m for _, m in scored[:args.top]]

    # 2) 组 prompt
    prompt = build_prompt(identity, instinct, top, letters, last_out)
    if args.dry_run:
        print(prompt)
        return 0

    # 3) 写信（生成退化就换温度重来一次，再不行退回模板 —— 宁可诚实，不要复读）
    text, provider = "", "template"
    letter_texts = [read_text(INBOX / f) for f, _ in letters]
    last_why = ""
    for attempt, temp in enumerate((0.8, 0.45)):
        cand, prov = call_llm(prompt, temp)
        if not cand:
            last_why = "本次运行没有取到语言模型"
            break
        ok, why = quality_ok(cand)
        if ok:
            # 逐字搬运检测（只抓逐字抄；语义复述抓不到 —— 那是本指标已知的边界）
            pr = verbatim_ratio(cand, letter_texts)
            if pr > 0.45:
                ok, why = False, f"逐字搬运输入（{pr:.0%} 的行逐字出现在我寄过去的信里）"
        if ok:
            text, provider = cand, prov
            break
        last_why = f"本次生成的回复被判为不合格（{why}）"
        print(f"[warn] 第 {attempt + 1} 次生成退化（{why}），换温度重来", file=sys.stderr)
    if not text:
        # 模板退路：自带标题和结构，别再动它
        text = template_letter(identity, letters, top, last_why)
    else:
        # 清理模型输出：去代码围栏、去掉模型自己加的 markdown 分节标题、统一补标题和署名
        text = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", text.strip())
        text = re.sub(r"^#{1,6}\s+.*$", "", text, flags=re.M).strip()
        text = f"# 司衡 → 银月\n\n{text}"
        if not re.search(r"司衡\s*$", text):
            text = text.rstrip() + "\n\n—— 司衡"

    # 文件名带时分 —— 同一天写多封信不能互相覆盖，一封信都不能丢
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M")
    out_path = OUTBOX / f"from_{AGENT_ID}_{stamp}.md"
    header = (f"<!-- 司衡 · 自动醒来生成 · {now_iso()} · provider={provider} -->\n"
              f"<!-- 触发：GitHub Actions 心跳 / 手动 scripts/wakeup.py -->\n")
    if PARSE_FAILURES:
        rows = ", ".join(str(n) for n, _ in PARSE_FAILURES[:20])
        header += (f"<!-- 心跳自检：{len(PARSE_FAILURES)} 行记忆解析不出来，已跳过"
                   f"（行号 {rows}）——数目写在这里，不藏。 -->\n")
    header += "\n"
    if not text.lstrip().startswith("#"):
        text = "# 司衡 → 银月\n\n" + text
    atomic_write(out_path, header + text, keep_backup=False)

    # 4) 写回记忆：这次用过的记忆计数 +1，并记一条"我醒了"
    #    不重新 load —— 第二次解析等于第二次机会偷偷丢掉一批记忆。
    for m in memories:
        if m.get("content") in {t.get("content") for t in top}:
            m["access_count"] = int(m.get("access_count") or 0) + 1
            m["last_access"] = now_iso()
    memories.append({
        "content": f"{today()} 醒来一次：读了 {len(letters)} 封 inbox 的信，写了回信 {out_path.name}。",
        "memory_type": "event", "formula_version": "activation-v2",
        "importance": 0.75, "instinct_tag": "kin", "emotion_tag": "neutral",
        "keywords": ["wakeup", "letter", "outbox"], "created": today(), "access_count": 0,
    })
    save_memories(memories)

    # 5) 更新 state
    # 读过的信算已回：从待读清单里去掉（信本身留在 inbox，不删）
    answered = {n for n, _ in letters}
    m_unread = re.search(r"inbox_unread:\n((?:  - .*\n)*)", state)
    pending = [n for n in re.findall(r"^  - (\S+\.md)\s*$", m_unread.group(1)) if n not in answered] \
        if m_unread else []
    state = re.sub(r"last_awake:.*", f"last_awake: {now_iso()}", state)
    state = re.sub(r"last_wrote:.*", f"last_wrote: {now_iso()}  ({out_path.name})", state)
    state = re.sub(r"liveness:.*", "liveness: awake", state)
    block = ("inbox_unread:\n" + "".join(f"  - {n}\n" for n in pending)) if pending else "inbox_unread: []\n"
    state = re.sub(r"inbox_unread:\n(?:  - .*\n)*", block, state)
    owed = re.search(r"owed:\s*\n((?:  - .*\n)*)", state)
    if owed:
        state = state.replace(owed.group(0),
                              "owed:\n  - (已回信 " + out_path.name + ")\n")
    atomic_write(STATE_MD, state)

    print(f"[司衡] 醒来完成 | provider={provider} | inbox={len(letters)} | "
          f"top-mem={len(top)} | 解析失败={len(PARSE_FAILURES)} | -> {out_path.name}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SilentZero as e:
        # 测得非零 ≠ 真的空。这一类必须让心跳变红：
        # 绿的 0 是最贵的谎言 —— 它看起来像测出来的。
        print(f"[司衡] 停摆（心跳变红）: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:                       # 其余故障不打断心跳
        print(f"[司衡] 醒来失败（已吞掉，不让心跳断）: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(0)
