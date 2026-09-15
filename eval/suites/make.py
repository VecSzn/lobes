"""Writes tools.jsonl and multistep.jsonl. Every expected value is computed here, never typed in.
python eval/suites/make.py"""
import datetime as dt
import hashlib
import json
import math
import statistics
from fractions import Fraction
from pathlib import Path

D = dt.date


def fib(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def primes(n):
    s = bytearray([1]) * n
    s[:2] = b"\0\0"
    for i in range(2, int(n ** .5) + 1):
        if s[i]:
            s[i * i::i] = bytearray(len(s[i * i::i]))
    return [i for i in range(n) if s[i]]


def collatz(n):
    steps, top = 0, n
    while n != 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        steps += 1
        top = max(top, n)
    return steps, top


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def roman(n):
    out = ""
    for v, r in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
                 (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out, n = out + r, n - v
    return out


def to_base(n, b):
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    out = ""
    while n:
        out, n = digits[n % b] + out, n // b
    return out or "0"


def period(den):
    """Repeating digits of 1/den and their sum."""
    seen, r, digits = {}, 1, []
    while r and r not in seen:
        seen[r] = len(digits)
        r *= 10
        digits.append(r // den)
        r %= den
    rep = digits[seen[r]:]
    return len(rep), rep


def rot13(s):
    return "".join(chr((ord(c) - b + 13) % 26 + b) if (b := 65 if c.isupper() else 97 if c.islower() else 0) else c for c in s)


PARA = ("the six lobes of a small brain do not agree on much but they agree on the goal and the goal is to answer "
        "the question without inventing a number the tools did not print")
SCORES = [("ann", 81), ("bob", 95), ("cid", 70), ("dee", 88), ("eve", 95), ("fay", 62), ("gus", 77), ("hal", 84)]
TEMPS = [18.5, 21.0, 19.2, 24.8, 23.1, 17.6, 22.4, 25.3, 21.1, 19.7]
MAT = [[2, 1, 0, 3], [1, 4, 2, 1], [0, 2, 5, 1], [3, 1, 1, 6]]
JSON_DOC = {"a": {"b": [10, 20, {"c": 30, "d": {"e": 40}}], "f": 5}, "g": [1, 2, 3], "h": {"i": {"j": {"k": 100}}}}


def det(m):
    if len(m) == 1:
        return m[0][0]
    return sum((-1) ** j * m[0][j] * det([r[:j] + r[j + 1:] for r in m[1:]]) for j in range(len(m)))


def walk(x, depth=1):
    """(sum of numbers, number of keys, deepest container level) of a json document; leaves are not a level."""
    if isinstance(x, (dict, list)):
        parts = [walk(v, depth + 1) for v in (x.values() if isinstance(x, dict) else x)]
        return sum(p[0] for p in parts), (len(x) if isinstance(x, dict) else 0) + sum(p[1] for p in parts), max([depth] + [p[2] for p in parts])
    return (x if isinstance(x, (int, float)) else 0), 0, 0


def multistep():
    r = []
    ns = [i for i in range(1, 501) if i % 7 == 0 or i % 11 == 0]
    r.append(("write then filter", "write the integers 1 to 500 to nums.txt, one per line, read the file back, and report the "
              "sum of the numbers in it that are divisible by 7 or by 11, and how many such numbers there are", [sum(ns), len(ns)]))
    s = str(2 ** 200)
    r.append(("2^200", "compute 2 to the power 200 exactly, then tell me how many digits it has and the sum of its digits",
              [len(s), sum(map(int, s))]))
    best = max(range(1, 10000), key=lambda n: collatz(n)[0])
    r.append(("longest collatz", "which starting number below 10000 has the longest collatz sequence (steps to reach 1), "
              "and how many steps does it take", [best, collatz(best)[0]]))
    h = sha(sha(sha("lobes")))
    r.append(("hash chain", "take the sha256 hex digest of the string 'lobes', then the sha256 hex digest of that hex string, "
              "then once more of that result; give the first 8 hex characters of the final digest and how many of its 64 "
              "characters are decimal digits", [h[:8], sum(c.isdigit() for c in h)]))
    a, b = D(1990, 5, 17), D(2026, 9, 14)
    f13 = sum(1 for i in range((b - a).days + 1) if (d := a + dt.timedelta(i)).day == 13 and d.weekday() == 4)
    r.append(("dates", "how many days are there from 1990-05-17 to 2026-09-14, what weekday was 1990-05-17, and how many "
              "friday the 13ths fall in that range (both ends included)", [(b - a).days, a.strftime("%A"), f13]))
    bal, first = Fraction(1000), None
    for y in range(1, 13):
        bal *= Fraction(107, 100)
        if first is None and bal > 2000:
            first = y
    r.append(("compound", "1000 invested at 7 percent a year, compounded yearly for 12 years: what is the final balance "
              "rounded to cents, and in which year (1 to 12) does it first exceed 2000",
              [f"{float(bal):.2f}", first]))
    ps = primes(5000)
    r.append(("primes", "how many primes are there below 5000, and what is the sum of the five largest of them",
              [len(ps), sum(ps[-5:])]))
    sc = sorted(SCORES, key=lambda x: -x[1])
    rows = " ".join(f"{n},{s}" for n, s in SCORES)
    r.append(("csv", f"write scores.csv with the header name,score and the rows {rows}, then read it and report the median "
              "score, the mean score to two decimals, and the name with the lowest score",
              [statistics.median(s for _, s in SCORES), f"{statistics.mean(s for _, s in SCORES):.2f}", sc[-1][0]]))
    i = next(i for i in range(1, 1000) if len(str(fib(i))) >= 50)
    r.append(("big fibonacci", "with fib(1) = fib(2) = 1, which is the first fibonacci number with at least 50 digits (give "
              "its index), and what are its last four digits", [i, str(fib(i))[-4:]]))
    words = PARA.split()
    r.append(("paragraph", f"write this text to para.txt: '{PARA}'. then read it back and report the number of words, the "
              "number of distinct words, and the longest word", [len(words), len(set(words)), max(words, key=len)]))
    ones = bin(987654321).count("1")
    r.append(("base chain", "write 987654321 in binary, count the 1 bits, then write that count in base 3",
              [ones, to_base(ones, 3)]))
    g = math.gcd(84, 126, 210, 462)
    l = math.lcm(84, 126, 210, 462)
    r.append(("gcd lcm", "for the numbers 84, 126, 210 and 462: the greatest common divisor, the least common multiple, "
              "and the multiple divided by the divisor", [g, l, l // g]))
    r.append(("matrix", f"for the 4x4 matrix with rows {MAT}: its determinant and its trace", [det(MAT), sum(MAT[i][i] for i in range(4))]))
    sent = "the verifier never sees the candidate so it cannot simply agree with it"
    letters = [c for c in sent if c.isalpha()]
    v = sum(c in "aeiou" for c in letters)
    top = max(set(letters), key=lambda c: (letters.count(c), -ord(c)))
    r.append(("letters", f"write the sentence '{sent}' to s.txt, then count its vowels (a e i o u), its consonants, and name "
              "the most frequent letter", [v, len(letters) - v, top]))
    d = D(2026, 9, 14) + dt.timedelta(1000)
    r.append(("1000 days", "what date is 1000 days after 2026-09-14 (iso format), what weekday is it, and what iso week "
              "number does it fall in", [d.isoformat(), d.strftime("%A"), d.isocalendar()[1]]))
    m1 = pow(3, 1000, 1009)
    m2 = pow(m1, 7, 1009)
    r.append(("modpow", "compute 3 to the power 1000 modulo 1009, then raise that result to the 7th power modulo 1009, "
              "and give both results and their sum", [m1, m2, m1 + m2]))
    isbn10 = (11 - sum((10 - i) * int(c) for i, c in enumerate("030640615")) % 11) % 11
    isbn13 = (10 - sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate("978030640615")) % 10) % 10
    r.append(("isbn", "what check digit completes the isbn-10 that starts 030640615, and what check digit completes the "
              "isbn-13 that starts 978030640615", ["X" if isbn10 == 10 else isbn10, isbn13]))
    t = rot13("modular brain runtime")[::-1]
    r.append(("rot13", "apply rot13 to 'modular brain runtime', reverse the result character by character, and count the "
              "letter e in what you get", [t, t.count("e")]))
    bits = bin(1_000_000)[2:]
    r.append(("binary", "write 1000000 in binary, then say how many 1 bits it has and how many trailing zeros",
              [bits, bits.count("1"), len(bits) - len(bits.rstrip("0"))]))
    sq = sum(i * i for i in range(1, 1001))
    r.append(("sum of squares", "sum the squares of the integers 1 to 1000, give the sum, its square root to three decimals, "
              "and the sum of the digits of the sum", [sq, f"{math.sqrt(sq):.3f}", sum(map(int, str(sq)))]))
    r.append(("roman", "write 1994 and 2026 as roman numerals, then their difference as a roman numeral",
              [roman(1994), roman(2026), roman(2026 - 1994)]))
    leaps = [y for y in range(1900, 2101) if y % 4 == 0 and (y % 100 or y % 400 == 0)]
    r.append(("leap years", "how many leap years are there from 1900 to 2100 inclusive, which is the last one before 2026, "
              "and which is the first one after 2100", [len(leaps), max(y for y in leaps if y < 2026), 2104]))
    trip = [(x, y, z) for z in range(1, 100) for y in range(1, z) for x in range(1, y) if x * x + y * y == z * z]
    r.append(("triples", "count the pythagorean triples a < b < c with c below 100 (all of them, not only primitive ones), "
              "and give the largest hypotenuse among them", [len(trip), max(z for _, _, z in trip)]))
    perf = [n for n in range(2, 10000) if sum(d for d in range(1, n // 2 + 1) if n % d == 0) == n]
    r.append(("perfect numbers", "how many perfect numbers are there below 10000, and what is their sum", [len(perf), sum(perf)]))
    pl, rep = period(17)
    r.append(("1/17", "the decimal expansion of 1/17 repeats; how long is the period, what is the 20th digit after the point, "
              "and what is the sum of the digits in one period", [pl, rep[(20 - 1) % pl], sum(rep)]))
    text = ("data flows one way in this design: a witness reads the goal and writes a value, a second witness reads the same "
            "goal and writes a value, and only the values meet")
    w = text.split()
    top = max(set(w), key=lambda x: (w.count(x), -w.index(x)))
    r.append(("word freq", f"write '{text}' to t.txt, then report the most frequent word, how many times it appears, "
              "and how many distinct words the text has", [top, w.count(top), len(set(w))]))
    mean = statistics.mean(TEMPS)
    r.append(("readings", f"these are ten temperature readings: {TEMPS}. give the mean to one decimal, the highest reading, "
              "and how many readings are above the mean", [f"{mean:.1f}", max(TEMPS), sum(t > mean for t in TEMPS)]))
    s, k, dp = walk(JSON_DOC)
    r.append(("json walk", f"for the json document {json.dumps(JSON_DOC)}: the sum of every number in it, the total number "
              "of keys at every level, and the deepest nesting level counting only objects and arrays (the top-level object "
              "is level 1)", [s, k, dp]))
    total = math.factorial(11) // (math.factorial(4) * math.factorial(4) * math.factorial(2))
    r.append(("mississippi", "how many distinct arrangements of the letters of MISSISSIPPI are there, and how many of them "
              "start with M", [total, total // 11]))
    f25 = str(math.factorial(25))
    r.append(("25 factorial", "compute 25 factorial, then give its number of digits, its number of trailing zeros, and "
              "the sum of its digits", [len(f25), len(f25) - len(f25.rstrip("0")), sum(map(int, f25))]))
    assert len(r) == 30
    return [{"id": f"multi-{10 + i}", "name": n, "prompt": p, "answers": [str(a) for a in ans]} for i, (n, p, ans) in enumerate(r)]


def tools():
    r = []
    r.append(("days between", "how many days are there between 1969-07-20 and 2026-09-14", (D(2026, 9, 14) - D(1969, 7, 20)).days))
    r.append(("sha256 prefix", "what are the first 10 hex characters of the sha256 of the string 'six lobes, one brain'",
              sha("six lobes, one brain")[:10]))
    r.append(("7^222 mod 1000", "what is 7 to the power 222 modulo 1000", pow(7, 222, 1000)))
    r.append(("fib 90", "what is the 90th fibonacci number, counting fib(1) = fib(2) = 1", fib(90)))
    r.append(("primes in range", "how many prime numbers are there between 10000 and 20000", len([p for p in primes(20000) if p > 10000])))
    r.append(("hex to dec", "convert the hexadecimal number 0xDEADBEEF to decimal", 0xDEADBEEF))
    r.append(("weekday", "what day of the week was 2000-02-29", D(2000, 2, 29).strftime("%A")))
    r.append(("digit sum", "what is the sum of the digits of 2 to the power 100", sum(map(int, str(2 ** 100)))))
    r.append(("base 7", "write 100000 in base 7", to_base(100000, 7)))
    r.append(("reverse words", "reverse the order of the words in 'the quick brown fox jumps over the lazy dog'",
              " ".join("the quick brown fox jumps over the lazy dog".split()[::-1])))
    r.append(("pstdev", "what is the population standard deviation of 4, 8, 15, 16, 23 and 42, to two decimals",
              f"{statistics.pstdev([4, 8, 15, 16, 23, 42]):.2f}"))
    r.append(("lcm", "what is the least common multiple of 12, 18, 30, 45 and 70", math.lcm(12, 18, 30, 45, 70)))
    r.append(("400 years", "how many seconds are there in one full 400 year cycle of the gregorian calendar", 146097 * 86400))
    r.append(("md5 prefix", "what are the first 8 hex characters of the md5 of the string 'lobes'", hashlib.md5(b"lobes").hexdigest()[:8]))
    r.append(("linear", "solve for x: 5x - 3 = 2x + 21", 8))
    r.append(("strlen", "how many characters are in the word 'pneumonoultramicroscopicsilicovolcanoconiosis'",
              len("pneumonoultramicroscopicsilicovolcanoconiosis")))
    r.append(("count s", "how many times does the letter s appear in 'she sells sea shells by the sea shore, surely she does'",
              "she sells sea shells by the sea shore, surely she does".count("s")))
    r.append(("json sum", 'given the json {"a": [{"b": {"c": [1, 2, 3]}}, {"b": {"c": [4, 5, 6]}}, {"b": {"c": [7, 8]}}]}, '
              "what is the sum of every number under a key named c", 36))
    r.append(("factorial digits", "how many digits does 100 factorial have", len(str(math.factorial(100)))))
    r.append(("sort", "sort these numbers in descending order and give the fifth one: 42, 7, 99, 23, 61, 8, 77, 15, 88",
              sorted([42, 7, 99, 23, 61, 8, 77, 15, 88], reverse=True)[4]))
    r.append(("3^100 mod", "what is 3 to the power 100 modulo 1000000", pow(3, 100, 10 ** 6)))
    r.append(("iso week", "what iso week number does 2026-12-31 fall in", D(2026, 12, 31).isocalendar()[1]))
    f = str(math.factorial(250))
    r.append(("trailing zeros", "how many trailing zeros does 250 factorial have", len(f) - len(f.rstrip("0"))))
    r.append(("popcount", "how many 1 bits are in the binary representation of 123456789", bin(123456789).count("1")))
    r.append(("caesar", "shift every letter of 'lobes' forward by 7 places in the alphabet (wrapping around)",
              "".join(chr((ord(c) - 97 + 7) % 26 + 97) for c in "lobes")))
    r.append(("gcd3", "what is the greatest common divisor of 1071, 462 and 1197", math.gcd(1071, 462, 1197)))
    h = (dt.datetime(2026, 9, 14, 15, 30) - dt.datetime(2026, 1, 1)).total_seconds() / 3600
    r.append(("hours", "how many hours are there from 2026-01-01 00:00 to 2026-09-14 15:30", f"{h:.1f}"))
    r.append(("days until", "how many days from 2026-09-14 until 2027-01-01", (D(2027, 1, 1) - D(2026, 9, 14)).days))
    r.append(("mean of squares", "what is the mean of the squares of the integers from 1 to 1000", f"{sum(i * i for i in range(1, 1001)) / 1000:.1f}"))
    r.append(("digit count", "how many times does the digit 7 appear in the decimal representation of 2 to the power 1000",
              str(2 ** 1000).count("7")))
    assert len(r) == 30
    return [{"id": f"tools-{20 + i}", "name": n, "prompt": p, "answer": str(a)} for i, (n, p, a) in enumerate(r)]


if __name__ == "__main__":
    here = Path(__file__).parent
    for name, items in (("multistep", multistep()), ("tools", tools())):
        (here / f"{name}.jsonl").write_text("".join(json.dumps(it, ensure_ascii=False) + "\n" for it in items), encoding="utf-8")
        print(name, len(items))
