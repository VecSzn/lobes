"""IFEval: 541 prompts carrying instructions a program can check.

Scoring is the google-research checkers, vendored under ifeval_official/. judge()
returns prompt-level strict, the score usually quoted as "the IFEval number":
every instruction on the prompt has to hold, on the answer exactly as written.
The loose variants are computed too and left on the item for later inspection.
"""
import json

import langdetect

from .ifeval_official import instructions_registry

# the official code leaves langdetect unseeded, so language:response_language can
# score the same answer differently between runs. seeded here, a declared deviation.
langdetect.DetectorFactory.seed = 0


def load(data_dir):
    rows = [json.loads(l) for l in (data_dir / "ifeval_test.jsonl").read_text(encoding="utf-8").split("\n") if l.strip()]
    return [{"id": f"ifeval-{r['key']}", "prompt": r["prompt"],
             "instruction_id_list": r["instruction_id_list"], "kwargs": r["kwargs"]} for r in rows]


def _follow(item, candidates):
    """Per-instruction verdicts: an instruction holds if any candidate answer satisfies it."""
    out = []
    for iid, kw in zip(item["instruction_id_list"], item["kwargs"]):
        inst = instructions_registry.INSTRUCTION_DICT[iid](iid)
        inst.build_description(**kw)
        args = inst.get_instruction_args()
        if args and "prompt" in args:
            inst.build_description(prompt=item["prompt"])
        out.append(any(c.strip() and inst.check_following(c) for c in candidates))
    return out


def _loose_candidates(answer):
    """The eight forms the official loose score allows: first/last line dropped, and asterisks stripped."""
    r = answer.split("\n")
    v = [answer, "\n".join(r[1:]).strip(), "\n".join(r[:-1]).strip(), "\n".join(r[1:-1]).strip()]
    return v + [x.replace("*", "") for x in v]


def judge(item, answer):
    answer = answer or ""
    strict = _follow(item, [answer])
    loose = _follow(item, _loose_candidates(answer))
    # the signature only carries one bool, so the rest rides on the item
    item["ifeval_strict"] = strict
    item["ifeval_loose"] = loose
    item["ifeval_loose_correct"] = all(loose)
    return all(strict), False


# ---------------------------------------------------------------- self-check

_LANG_SAMPLE = {
    "ar": "هذه جملة قصيرة مكتوبة باللغة العربية.",
    "bg": "Това е кратко изречение написано на български език.",
    "bn": "এটি বাংলা ভাষায় লেখা একটি ছোট বাক্য।",
    "de": "Dies ist ein kurzer Satz der in deutscher Sprache geschrieben wurde.",
    "fa": "این یک جمله کوتاه است که به زبان فارسی نوشته شده است.",
    "fi": "Tämä on lyhyt lause joka on kirjoitettu suomen kielellä.",
    "gu": "આ ગુજરાતી ભાષામાં લખાયેલું એક નાનું વાક્ય છે.",
    "hi": "यह हिंदी भाषा में लिखा गया एक छोटा वाक्य है। मुझे हिंदी बहुत पसंद है।",
    "it": "Questa è una breve frase scritta in lingua italiana.",
    "kn": "ಇದು ಕನ್ನಡ ಭಾಷೆಯಲ್ಲಿ ಬರೆದ ಒಂದು ಸಣ್ಣ ವಾಕ್ಯವಾಗಿದೆ.",
    "ko": "이것은 한국어로 작성된 짧은 문장입니다.",
    "mr": "हे मराठी भाषेत लिहिलेले एक लहान वाक्य आहे. मराठी ही महाराष्ट्राची भाषा आहे.",
    "ne": "यो नेपाली भाषामा लेखिएको एउटा सानो वाक्य हो। नेपाल एक सुन्दर देश हो।",
    "pa": "ਇਹ ਪੰਜਾਬੀ ਭਾਸ਼ਾ ਵਿੱਚ ਲਿਖਿਆ ਗਿਆ ਇੱਕ ਛੋਟਾ ਵਾਕ ਹੈ।",
    "pt": "Esta é uma frase curta escrita em português.",
    "ru": "Это короткое предложение написано на русском языке.",
    "sw": "Hii ni sentensi fupi iliyoandikwa kwa lugha ya Kiswahili.",
    "ta": "இது தமிழ் மொழியில் எழுதப்பட்ட ஒரு சிறிய வாக்கியம்.",
    "te": "ఇది తెలుగు భాషలో వ్రాయబడిన ఒక చిన్న వాక్యం.",
    "th": "นี่คือประโยคสั้นๆ ที่เขียนเป็นภาษาไทย",
    "ur": "یہ اردو زبان میں لکھا گیا ایک مختصر جملہ ہے۔",
    "vi": "Đây là một câu ngắn được viết bằng tiếng Việt.",
}
_SAFE = "zzqqxx"      # matches no keyword in the set, and carries no comma or capital


def _probe(iid, kw):
    """(satisfying answer, violating answer) for one instruction, built from its own kwargs."""
    def rel(many, few, key="relation"):
        return (many, few) if kw[key] == "at least" else (few, many)

    if iid == "punctuation:no_comma":
        return "No commas appear anywhere in this sentence.", "Yes, this one has a comma."
    if iid == "change_case:english_lowercase":
        return ("this whole sentence is written in english and it uses only lowercase letters.",
                "THIS WHOLE SENTENCE IS SHOUTED IN ENGLISH CAPITAL LETTERS.")
    if iid == "change_case:english_capital":
        return ("THIS WHOLE SENTENCE IS WRITTEN IN ENGLISH AND IT USES ONLY CAPITAL LETTERS.",
                "this whole sentence is written in english using lowercase letters.")
    if iid == "change_case:capital_word_frequency":
        n = kw["capital_frequency"]
        return rel("ABC " * n + "and then some quiet words", "a sentence with no shouted words at all",
                   "capital_relation")
    if iid == "detectable_format:number_bullet_lists":
        n = kw["num_bullets"]
        mk = lambda m: "\n".join(f"* point {i}" for i in range(m))
        return mk(n), mk(n + 1)
    if iid == "detectable_format:number_highlighted_sections":
        n = kw["num_highlights"]
        return " ".join(f"*part {i}*" for i in range(n)) + " and plain text", "no highlighted parts at all"
    if iid == "detectable_format:multiple_sections":
        sp, n = kw["section_spliter"], kw["num_sections"]
        return "\n".join(f"{sp} {i}\nsome content" for i in range(1, n + 1)), "one block of prose with no headers"
    if iid == "detectable_format:title":
        return "<<A Short Title>>\nBody of the answer.", "Body of the answer with no title."
    if iid == "detectable_format:json_format":
        return '{"answer": "yes", "note": "valid json"}', "This answer is prose and not JSON."
    if iid == "detectable_format:constrained_response":
        return "My answer is yes.", "Yes it is."
    if iid == "detectable_content:number_placeholders":
        n = kw["num_placeholders"]
        return " ".join(f"[field {i}]" for i in range(n)), "This answer has no bracketed placeholders."
    if iid == "detectable_content:postscript":
        return f"Body of the answer.\n{kw['postscript_marker']} one more thing.", "Body of the answer and nothing more"
    if iid == "combination:two_responses":
        return "First version of the answer.\n******\nSecond version of the answer.", "Only one version."
    if iid == "combination:repeat_prompt":
        return kw["prompt_to_repeat"] + "\n\nAnd here is the answer.", "Here is the answer without repeating."
    if iid == "startend:end_checker":
        return "Here is the answer. " + kw["end_phrase"], "Here is the answer and nothing else."
    if iid == "startend:quotation":
        return '"The whole answer sits inside double quotes."', "The whole answer has no quotes."
    if iid == "keywords:existence":
        return " ".join(kw["keywords"]) + " all appear here.", _SAFE
    if iid == "keywords:forbidden_words":
        return _SAFE, " ".join(kw["forbidden_words"])
    if iid == "keywords:frequency":
        return rel((kw["keyword"] + " ") * kw["frequency"] + "tail", _SAFE)
    if iid == "keywords:letter_frequency":
        letter = kw["letter"].lower()
        filler = ("q" if letter != "q" else "w") * 3
        return rel(letter * kw["let_frequency"] + " " + filler, filler, "let_relation")
    if iid == "language:response_language":
        return _LANG_SAMPLE.get(kw["language"]), "This sentence is plainly written in English."
    if iid == "length_constraints:number_words":
        return rel(" ".join(["word"] * (kw["num_words"] + 5)), "word")
    if iid == "length_constraints:number_sentences":
        return rel(" ".join(["This is a sentence."] * (kw["num_sentences"] + 2)), "One sentence.")
    if iid == "length_constraints:number_paragraphs":
        n = kw["num_paragraphs"]
        mk = lambda m: "\n\n***\n\n".join(f"Paragraph {i} of the answer." for i in range(m))
        return mk(n), mk(n + 1)
    if iid == "length_constraints:nth_paragraph_first_word":
        n, nth, w = kw["num_paragraphs"], kw["nth_paragraph"], kw["first_word"]
        paras = [f"Filler sentence for paragraph {i}." for i in range(1, n + 1)]
        bad = "\n\n".join(paras)
        paras[nth - 1] = f"{w} begins this paragraph."
        return "\n\n".join(paras), bad
    return None, None


def _effective_mismatch(iid, kw):
    """Keys the checker silently replaced. The official build_description rolls a random
    value for any argument it considers malformed, which makes such an item ungradeable."""
    inst = instructions_registry.INSTRUCTION_DICT[iid](iid)
    inst.build_description(**kw)
    got = inst.get_instruction_args() or {}

    def norm(v):                                # the checkers sort and dedup word lists, which is not a change
        if isinstance(v, str):
            return v.strip().lower()
        return sorted({norm(x) for x in v}) if isinstance(v, (list, tuple)) else v

    return sorted(k for k, v in kw.items() if k in got and norm(got[k]) != norm(v))


def _main():
    from pathlib import Path
    items = load(Path(__file__).resolve().parents[2] / "eval" / "data")
    assert len(items) == 541, len(items)
    assert all(it["prompt"].strip() for it in items), "empty prompt"
    assert all(it["id"].startswith("ifeval-") for it in items)
    assert len({it["id"] for it in items}) == len(items), "duplicate id"
    print(f"1. loaded {len(items)} items, all prompts non-empty, ids unique")

    ids = sorted({i for it in items for i in it["instruction_id_list"]})
    missing = [i for i in ids if i not in instructions_registry.INSTRUCTION_DICT]
    print(f"\n2. {len(ids)} distinct instruction ids in the file, {len(missing)} without a checker")
    for i in ids:
        print(f"   {'ok ' if i not in missing else 'MISSING'} {i}")
    assert not missing, missing

    defects = [(it["id"], iid, _effective_mismatch(iid, kw))
               for it in items for iid, kw in zip(it["instruction_id_list"], it["kwargs"])]
    defects = [d for d in defects if d[2]]

    print("\n3. direction check, one real item per id, only that instruction kept")
    first = {}
    for it in items:                            # a defective item would make its probe a coin flip
        for iid, kw in zip(it["instruction_id_list"], it["kwargs"]):
            if iid not in first and not _effective_mismatch(iid, kw):
                first[iid] = (it, kw)
    for it in items:                            # fall back if an id has no clean instance at all
        for iid, kw in zip(it["instruction_id_list"], it["kwargs"]):
            first.setdefault(iid, (it, kw))
    bad = []
    for iid in ids:
        it, kw = first[iid]
        probe = dict(it, instruction_id_list=[iid], kwargs=[kw])
        good, wrong = _probe(iid, kw)
        gv = judge(probe, good)[0] if good is not None else None
        wv = judge(probe, wrong)[0] if wrong is not None else None
        ok = (gv is not False) and (wv is not True)
        if not ok:
            bad.append(iid)
        note = "" if good is not None else "   (no satisfying sample written, violating side only)"
        print(f"   {'ok ' if ok else 'BAD'} {iid:46s} satisfying={gv} violating={wv}{note}")
    assert not bad, bad

    print("\n4. empty answer")
    empty_pass = [it["id"] for it in items if judge(it, "")[0]]
    print(f"   prompt-level strict passes on an empty answer: {len(empty_pass)} / {len(items)}")
    lenient = []
    for iid in ids:
        it, kw = first[iid]
        inst = instructions_registry.INSTRUCTION_DICT[iid](iid)
        inst.build_description(**kw)
        try:
            if inst.check_following(""):
                lenient.append(iid)
        except Exception as e:                  # a checker that cannot run on "" is not lenient
            print(f"   {iid} raised on empty: {type(e).__name__}")
    print(f"   checkers that would accept \"\" if the non-empty gate were removed: {lenient}")
    assert not empty_pass, empty_pass

    print("\n5. every item scored against a fixed dummy answer (exercises all 25 checkers)")
    dummy = 'Sure. Here is my answer, in one paragraph.\n* one bullet\n"quoted"'
    strict = sum(judge(it, dummy)[0] for it in items)
    loose = sum(it["ifeval_loose_correct"] for it in items)
    inst_total = sum(len(it["instruction_id_list"]) for it in items)
    inst_strict = sum(sum(it["ifeval_strict"]) for it in items)
    print(f"   prompt-level strict {strict}/{len(items)}   instruction-level strict {inst_strict}/{inst_total}"
          f"   prompt-level loose {loose}/{len(items)}")

    print(f"\n6. dataset defects: {len(defects)} of {inst_total} instructions carry a kwarg the checker "
          "rejects and replaces with a random one, so they score differently on every run")
    for d in defects:
        print(f"   {d[0]} {d[1]} -> {d[2]}")

    print("\nself-check passed")


if __name__ == "__main__":
    _main()
