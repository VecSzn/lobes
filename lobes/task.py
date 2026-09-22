"""What one request carries through the relay: its budgets and its state. The runner and every lobe share this."""
import re
import threading
import time
from dataclasses import dataclass, field

EFFORT = {   # same relay at every level, only the budgets change
    "low":    dict(think=1024, calls=12, tokens=16384, seconds=180),
    "medium": dict(think=4096, calls=20, tokens=32768, seconds=420),
    "high":   dict(think=8192, calls=30, tokens=65536, seconds=900),
}
# seconds is checked before each call and caps each HTTP wait. loads and tool runs aren't timed, so a request can run over.
ALIASES = {"auto": "medium", "xhigh": "high", "max": "high"}    # old names some clients still send
ANSWER = 4096        # tokens a reply may use after its thinking
CONCLUSION = 2048    # room to ask for just the conclusion when a reply runs out; sized on GPQA replies
SIMPLE_THINK = 1024  # a simple request still thinks a little: with none, the solver guessed the time instead of calling python


class BudgetExceeded(Exception):
    pass


def effort(cfg):
    level = cfg.get("effort") or "medium"
    level = ALIASES.get(level, level)
    if level not in EFFORT:
        raise ValueError(f"unknown effort {level!r}; use low, medium or high")
    return EFFORT[level]


@dataclass
class Turn:
    """What the relay decided about the request. A client's tool step comes back as a new request and gets it back."""
    route: str = "hard"              # the executive's call: simple | hard
    topic: str | None = None         # the router's call where the profile has experts: the slot that answers
    now: str = ""                    # local time when the request came in; a client's tool steps keep the first one
    requirements: str = ""           # what the review model read the request as asking the reply to satisfy
    problems: list = field(default_factory=list)       # what the review sent back
    checked: int = 0                 # how many of the answer's checks have run, across the turn's tool steps


@dataclass
class Work:
    """What the lobes build while they solve it."""
    messages: list = field(default_factory=list)       # the reasoning conversation, tool turns included
    ran: list = field(default_factory=list)            # (tool, args, result text) for each call reasoning made
    tool_results: dict = field(default_factory=dict)   # ref -> raw result dict
    observations: list = field(default_factory=list)   # Observation: what perception and ocr read from images
    draft: str | None = None         # reasoning's latest reply


@dataclass
class Spend:
    """What the request has used, against its level's caps."""
    calls: list = field(default_factory=list)          # (lobe, model, ms, tokens)
    usage: dict = field(default_factory=dict)          # prompt/completion/total tokens summed over calls
    context: dict = field(default_factory=dict)        # usage of reasoning's first call: how long the conversation is
    swaps: int = 0
    capped: str | None = None        # which cap ended the request, if one did
    t0: float = field(default_factory=time.perf_counter)

    def ms(self):
        return int((time.perf_counter() - self.t0) * 1000)

    def over(self, caps):
        """-> the cap this request has reached, or None. Once one is reached it stays."""
        if self.capped:
            return self.capped
        if len(self.calls) >= caps["calls"]:
            self.capped = "calls"
        elif self.usage.get("completion_tokens", 0) >= caps["tokens"]:
            self.capped = "tokens"
        elif self.ms() >= caps["seconds"] * 1000:
            self.capped = "seconds"
        return self.capped


@dataclass
class TaskState:
    """The request as it came in, three groups filled while it runs, and the reply."""
    task_id: str
    goal: str
    images: list
    earlier: str = ""                # the user's previous message, for the router
    continues: bool = False          # the request carries results of tool calls the client ran for it
    step: str | None = None          # id of the latest tool call in the client's conversation
    client_tools: list | None = None # tools the api client runs itself; a call to one ends the request.
                                     # None when it offers none: the request then runs on the local tools
    on_delta: object = None          # on_delta(kind, text) streams the work; kind is "reasoning", "step" or "content"
    cancel: threading.Event = field(default_factory=threading.Event)   # set when the api client goes away
    effort: str = "medium"
    turn: Turn = field(default_factory=Turn)
    work: Work = field(default_factory=Work)
    spend: Spend = field(default_factory=Spend)
    answer: str | None = None
    tool_calls: list = field(default_factory=list)     # calls to the client's tools, for the client to run
    uncertainties: list = field(default_factory=list)
    stopped: bool = False            # stop() ended it: the answer says why, after any text already streamed

    def __post_init__(self):
        # Codex sometimes sends only hosted tools, which responses.py drops. an empty list has to mean "none
        # offered", or the request ends up with no tools at all
        self.client_tools = self.client_tools or None

    def summary(self):
        toks = sum(c[3] for c in self.spend.calls)
        return (f"{self.turn.route} calls={len(self.spend.calls)} tools={len(self.work.tool_results)} "
                f"sent_back={len(self.turn.problems)} swaps={self.spend.swaps} tokens={toks} {self.spend.ms()} ms"
                + (f" capped={self.spend.capped}" if self.spend.capped else ""))


def say(state, text):
    """Shows a step of the relay to a streaming client. Chat completions sends it as reasoning; responses.py makes it
    the status label and the first line of the thinking after it."""
    if state.on_delta:
        state.on_delta("step", text)


def stop(state, zh, en):
    """Ends the request with this answer, in the request's language. A rewrite that stops keeps the reviewed draft."""
    text = zh if re.search(r"[一-鿿]", state.goal) else en
    if state.work.draft:    # a repair: the draft ships as when the last check rejects it, with why the rewrite stopped
        state.uncertainties += [f"A review still found a problem: {p}" for p in state.turn.problems[-1:]] + [text.splitlines()[0]]
    else:
        state.answer, state.stopped = text, True
    raise BudgetExceeded(en.splitlines()[0])


def stop_repeating(state):
    name, _, out = state.work.ran[-1]
    stop(state, f"{name} 三次返回同样的结果，已停下，没有再发同样的调用：\n\n{out[-3000:]}",
         f"{name} returned the same result three times, so I stopped instead of sending it again:\n\n{out[-3000:]}")
