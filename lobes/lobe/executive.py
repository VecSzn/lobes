"""Executive: decides whether a request is simple or hard, and in a profile with experts which expert answers it.
A simple one gets a quick answer from reasoning, tools included; a hard one gets thinking at the request's level and
a review. The difficulty model is a classifier tuned to answer easy, medium or hard to the prompt below."""
import dataclasses
import threading
import time

SYS = ("You are a query difficulty classifier for an LLM routing system.\nClassify each query as easy, medium, or hard "
       "based on the cognitive depth and domain expertise required to answer correctly.\n"
       "Respond with ONLY one word: easy, medium, or hard.")
LEVELS = ["easy", "medium", "hard"]     # a grammar holds the reply to one of these

TOPICS = {       # a profile fills the ones it has an expert for; the router reads these lines
    "chat": "greetings, thanks, small talk, or a short everyday question with nothing to look up or work out",
    "code": "writing, fixing, explaining or running code; shell commands; anything about files, folders or projects on the computer",
    "math": "a number or quantity to work out: arithmetic, word problems, algebra, probability, dates, units",
    "documents": "the text to work on is pasted into the message itself: summarize, translate, rewrite, or answer from it",
    "knowledge": "a fact or explanation about the world: history, science, people, places, events",
}
EXAMPLES = [
    ("hey, how's it going", "chat"),
    ("Complete this python function: def is_palindrome(s: str):", "code"),
    ("why does open() raise UnicodeDecodeError on this csv", "code"),
    ("what is the project in ~/work/site about", "code"),
    ("看一下 C:\\data 里的 notes.md 讲了什么", "code"),
    ("A train leaves at 3pm going 60 mph. How far has it gone by 5:30pm?", "math"),
    ("what is 2 to the power 100 modulo 97", "math"),
    ("Summarize this contract in five bullet points: <the contract>", "documents"),
    ("who painted the Mona Lisa", "knowledge"),
    ("what caused the fall of the Western Roman Empire", "knowledge"),
]
_seen = {}       # (profile, tool call id) -> the Turn of the request that made the call
_lock = threading.Lock()


def ends(text, head=1200, tail=400):
    """The start and end of a long request, where the ask usually is; a whole document on the CPU takes seconds."""
    text = text.strip()
    return text if len(text) <= head + tail else f"{text[:head]}\n...\n{text[-tail:]}"


def intake(ctx, state):
    # a client's tool step continues the request that made the call, even after the client compacted that request away
    with _lock:     # remember() trims the oldest entries under the same lock
        hit = _seen.get((ctx.profile, state.step)) if state.continues else None
    if hit:
        state.turn = dataclasses.replace(hit, problems=list(hit.problems))
        return
    state.turn.now = time.strftime("%Y-%m-%d %A %H:%M")
    topics = [t for t in TOPICS if ctx.is_model(t)]
    if topics and ctx.is_model("router") and not state.images:
        state.turn.topic = route(ctx, state, topics)
    if state.images or not ctx.is_model("executive"):
        return                  # images go through perception and a review; without a classifier everything is hard
    r = ctx.chat(state, "executive", [{"role": "system", "content": SYS},
                                      {"role": "user", "content": "Classify: " + ends(state.goal)}],
                 choices=LEVELS, temperature=0, max_tokens=5)
    state.turn.route = "simple" if r.data == "easy" else "hard"     # medium, hard, or no label: thinking and a review


def remember(ctx, state):
    """Called when a reply hands tool calls to the client."""
    with _lock:
        for call in state.tool_calls:
            _seen[(ctx.profile, call.get("id"))] = dataclasses.replace(state.turn, problems=list(state.turn.problems))
        while len(_seen) > 1024:        # oldest first, so other sessions' live turns keep theirs
            _seen.pop(next(iter(_seen)))


def route(ctx, state, topics):
    """-> the topic whose expert answers, or None when the router's reply does not parse."""
    sys = ("Pick the expert for the user's latest message.\n"
           + "\n".join(f"- {t}: {TOPICS[t]}" for t in topics)
           + "\nExamples:\n" + "\n".join(f'"{q}" -> {t}' for q, t in EXAMPLES if t in topics))
    ask = (f"Their previous message: {ends(state.earlier, 300, 100)}\n\nLatest: " if state.earlier else "") + ends(state.goal)
    schema = {"type": "object", "additionalProperties": False, "required": ["topic"],
              "properties": {"topic": {"enum": topics}}}
    r = ctx.chat(state, "router", [{"role": "system", "content": sys}, {"role": "user", "content": ask}],
                 schema=schema, temperature=0, max_tokens=20)
    return r.data["topic"] if r.data else None
