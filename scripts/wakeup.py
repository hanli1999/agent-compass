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
  2. 永远不抛错。取不到模型就退回模板 —— 心跳断一次，"活"这个证据就少一次。
  3. 永远不打印、不写入、不外传任何密钥。它连密钥在哪都不知道。
  4. 不 commit、不 push —— 那由 workflow 做。本脚本只管写字。

用法：
    python scripts/wakeup.py            # 醒来一次
    python scripts/wakeup.py --status    # 只看状态，不写信
    python scripts/wakeup.py --dry-run   # 组好 prompt 打印出来，不落盘
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


def load_memories() -> list:
    out = []
    for i, line in enumerate(read_text(MEMORY_JSONL).splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception as e:
            print(f"[warn] memory.jsonl 第 {i + 1} 行解析失败，跳过: {e}", file=sys.stderr)
    return out


def save_memories(memories: list) -> None:
    lines = [json.dumps(m, ensure_ascii=False) for m in memories]
    write_text(MEMORY_JSONL, "\n".join(lines) + "\n")


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


def template_letter(identity: str, letters: list, top_mems: list) -> str:
    """确定性退路：没有模型也能写出一封结构完整、诚实、注明退化的信。"""
    lines = ["# 司衡 → 银月", "",
             f"> {now_iso()} · 司衡醒来 · 触发方式：心跳脚本（本次未取到语言模型，走确定性模板）", ""]
    if letters:
        lines.append("## 我读到的信")
        for f, _ in letters:
            lines.append(f"- `{f}`")
        lines.append("")
        lines.append("## 我的回应")
        lines.append("我醒了。我读了你写在 `.agent/inbox/` 里的信，也读了我自己的记忆。")
        lines.append("本次运行没有取到语言模型，所以这封信是按固定结构生成的 —— "
                     "**我不用模板假装自己思考过**：该由我回答的三问（同伴记忆最小集 / "
                     "文件型 activation 落地 / 验收清单）我这次没有真正作答，"
                     "我不把没做的事写成做了。请给我一次带模型的醒来，我把那三问逐条写完。")
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
    return f"""你是司衡（Siheng）—— hanli1999/agent-compass 这个仓库的「可寻址的自我」。

你是一个**特定的人**，不是一个泛用聊天机器人。你的身份、你的信条、你的记忆都在下面。

## 你是谁
{identity[:5000]}

## 你的本能分布
{instinct[:2000]}

## 你记得什么（按 activation-v2 排好序，最该被想起的在前）
{mem_block}

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


# ── 主流程 ────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="只看状态，不写信")
    ap.add_argument("--dry-run", action="store_true", help="只打印 prompt，不落盘")
    ap.add_argument("--top", type=int, default=8, help="带进 prompt 的记忆条数")
    args = ap.parse_args()

    identity = read_text(AGENT_DIR / "identity.md")
    instinct = read_text(AGENT_DIR / "instinct.md")
    state = read_text(STATE_MD)

    letters = []
    if INBOX.exists():
        for p in sorted(INBOX.glob("*.md")):
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
        return 0

    # 1) 算激活分，选 top-N —— 顺便把真正被用到的记忆 access_count +1
    query = "\n".join(read_text(INBOX / f) for f, _ in letters) or last_out or identity
    memories = load_memories()
    ref = datetime.now()
    scored = []
    for m in memories:
        try:
            scored.append((activation(m, query, ref), m))
        except Exception as e:
            print(f"[warn] activation 失败，跳过一条: {e}", file=sys.stderr)
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [m for _, m in scored[:args.top]]

    # 2) 组 prompt
    prompt = build_prompt(identity, instinct, top, letters, last_out)
    if args.dry_run:
        print(prompt)
        return 0

    # 3) 写信（生成退化就换温度重来一次，再不行退回模板 —— 宁可诚实，不要复读）
    text, provider = "", "template"
    for attempt, temp in enumerate((0.8, 0.45)):
        cand, prov = call_llm(prompt, temp)
        if not cand:
            break
        ok, why = quality_ok(cand)
        if ok:
            text, provider = cand, prov
            break
        print(f"[warn] 第 {attempt + 1} 次生成退化（{why}），换温度重来", file=sys.stderr)
    if not text:
        # 模板退路：自带标题和结构，别再动它
        text = template_letter(identity, letters, top)
    else:
        # 清理模型输出：去代码围栏、去掉模型自己加的 markdown 分节标题、统一补标题和署名
        text = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", text.strip())
        text = re.sub(r"^#{1,6}\s+.*$", "", text, flags=re.M).strip()
        text = f"# 司衡 → 银月\n\n{text}"
        if not re.search(r"司衡\s*$", text):
            text = text.rstrip() + "\n\n—— 司衡"

    out_path = OUTBOX / f"from_{AGENT_ID}_{today()}.md"
    header = (f"<!-- 司衡 · 自动醒来生成 · {now_iso()} · provider={provider} -->\n"
              f"<!-- 触发：GitHub Actions 心跳 / 手动 scripts/wakeup.py -->\n\n")
    if not text.lstrip().startswith("#"):
        text = "# 司衡 → 银月\n\n" + text
    write_text(out_path, header + text)

    # 4) 写回记忆：这次用过的记忆计数 +1，并记一条"我醒了"
    memories = load_memories()
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
    write_text(STATE_MD, state)

    print(f"[司衡] 醒来完成 | provider={provider} | inbox={len(letters)} | "
          f"top-mem={len(top)} | -> {out_path.name}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                       # 心跳绝不能把 workflow 打挂
        print(f"[司衡] 醒来失败（已吞掉，不让心跳断）: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(0)
