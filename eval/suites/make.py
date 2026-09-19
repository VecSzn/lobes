"""Writes tools.jsonl and multistep.jsonl. Every expected value is computed here, never typed in.
python eval/suites/make.py
Two splits per suite: v3 items keep their ids and values untouched, v4 items are the harder ones
added in the round after."""
import base64
import datetime as dt
import hashlib
import json
import math
import re
import statistics
import zlib
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


def lcs(a, b):
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b):
            cur.append(prev[j] + 1 if ca == cb else max(cur[j], prev[j + 1]))
        prev = cur
    return prev[-1]


def edits(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(prev[j - 1] if ca == cb else 1 + min(prev[j - 1], prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def factorise(n):
    f, p = {}, 2
    while p * p <= n:
        while n % p == 0:
            f[p] = f.get(p, 0) + 1
            n //= p
        p += 1
    if n > 1:
        f[n] = f.get(n, 0) + 1
    return f


def totient(n):
    r = n
    for p in factorise(n):
        r -= r // p
    return r


def ndivisors(n):
    r = 1
    for e in factorise(n).values():
        r *= e + 1
    return r


def proper_divisor_sums(n):
    """s[m] = sum of the divisors of m below m, for every m < n"""
    s = [0] * n
    for d in range(1, n // 2 + 1):
        for m in range(2 * d, n, d):
            s[m] += d
    return s


def partitions(n):
    dp = [1] + [0] * n
    for k in range(1, n + 1):
        for i in range(k, n + 1):
            dp[i] += dp[i - k]
    return dp[n]


def collatz_peak(n):
    top = n
    while n != 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        top = max(top, n)
    return top


def parse_roman(s):
    vals, total, prev = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}, 0, 0
    for c in reversed(s):
        v = vals[c]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


def rle(s):
    out, i = [], 0
    while i < len(s):
        j = i
        while j < len(s) and s[j] == s[i]:
            j += 1
        out.append(f"{s[i]}{j - i}")
        i = j
    return "".join(out)


def change(amount, coins):
    dp = [1] + [0] * amount
    for c in coins:
        for i in range(c, amount + 1):
            dp[i] += dp[i - c]
    return dp[amount]


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


LCS_A = "the reasoning lobe hands over a program that recomputes the answer"
LCS_B = "the motor lobe writes a program from the goal alone and prints the answer"
EDIT_A = "a witness that ran a program beats a witness that wrote a number"
EDIT_B = "two witnesses that agree beat one witness that sounds certain"
MAT5 = [[3, 1, 4, 1, 5], [9, 2, 6, 5, 3], [5, 8, 9, 7, 9], [3, 2, 3, 8, 4], [6, 2, 6, 4, 3]]
WORDS = ["listen", "silent", "enlist", "google", "banana", "inlets", "elbow", "below", "bowel", "state",
         "taste", "tates", "cat", "act", "tac", "night", "thing", "stone", "tones", "notes", "onset", "seton"]


def tools_v4():
    """30 harder one-value items, the v4 half. Nothing is typed in; every value comes from the line above it."""
    r = []
    r.append(("modinv", "what is the modular inverse of 987654321 modulo 1000000007, given as the integer "
              "between 0 and 1000000006", pow(987654321, -1, 10 ** 9 + 7)))
    r.append(("20000th prime", "what is the 20000th prime number, counting 2 as the first",
              primes(230000)[19999]))
    r.append(("totient", "how many integers from 1 to 1234567890 inclusive are coprime to 1234567890",
              totient(1234567890)))
    r.append(("divisor count", "how many positive divisors does 123456789000 have", ndivisors(123456789000)))
    r.append(("binomial digits", "how many digits does the binomial coefficient 200 choose 100 have",
              len(str(math.comb(200, 100)))))
    r.append(("catalan", "what is catalan(30), where catalan(n) = (2n choose n) divided by (n + 1)",
              math.comb(60, 30) // 31))
    r.append(("lcs", "what is the length of the longest common subsequence of these two strings, counting "
              f"characters including spaces: '{LCS_A}' and '{LCS_B}'", lcs(LCS_A, LCS_B)))
    r.append(("edit distance", f"what is the levenshtein edit distance between '{EDIT_A}' and '{EDIT_B}', where one "
              "substitution, one insertion or one deletion each cost 1", edits(EDIT_A, EDIT_B)))
    r.append(("base64", "what is the base64 encoding of the ascii string 'six lobes, one brain, no blackboard'",
              base64.b64encode(b"six lobes, one brain, no blackboard").decode()))
    r.append(("crc32", "what is the crc32 checksum of the ascii bytes of 'lobes', as an unsigned decimal integer",
              zlib.crc32(b"lobes")))
    r.append(("sum of primes", "what is the sum of all prime numbers below 200000", sum(primes(200000))))
    r.append(("determinant 5x5", f"what is the determinant of the 5x5 matrix with rows {MAT5}", det(MAT5)))
    r.append(("partitions", "in how many ways can the integer 60 be written as a sum of positive integers, "
              "where order does not matter", partitions(60)))
    r.append(("collatz peak", "starting from 703 and applying n -> n/2 when n is even and n -> 3n+1 when n is odd, "
              "what is the largest value the sequence reaches before it gets to 1", collatz_peak(703)))
    s = proper_divisor_sums(20000)
    amic = sorted({n for n in range(2, 20000) if s[n] < 20000 and s[n] != n and s[s[n]] == n})
    r.append(("amicable", "what is the sum of all amicable numbers below 20000, where n is amicable if the sum of "
              "its proper divisors is some m != n and the sum of the proper divisors of m is n", sum(amic)))
    r.append(("bit count range", "what is the total number of 1 bits in the binary representations of all the "
              "integers from 1 to 100000 inclusive", sum(bin(i).count("1") for i in range(1, 100001))))
    hay = "abababababaababababaabababa"
    ov = sum(1 for i in range(len(hay) - 2) if hay[i:i + 3] == "aba")
    r.append(("overlapping", f"how many times does 'aba' occur in '{hay}', counting overlapping occurrences",
              ov))
    r.append(("parse roman", "what number is the roman numeral MCMXCIV plus the roman numeral MMCDXLIV, as a decimal",
              parse_roman("MCMXCIV") + parse_roman("MMCDXLIV")))
    a, b = D(2026, 1, 1), D(2026, 12, 31)
    bd = sum(1 for i in range((b - a).days + 1) if (a + dt.timedelta(i)).weekday() < 5)
    r.append(("business days", "how many days from 2026-01-01 to 2026-12-31 inclusive fall on a monday, tuesday, "
              "wednesday, thursday or friday", bd))
    r.append(("digit frequency", "how many times does the digit 5 appear in the decimal representation of 3 to the "
              "power 1000", str(3 ** 1000).count("5")))
    ps = primes(10000)
    gap = max(ps[i + 1] - ps[i] for i in range(len(ps) - 1))
    r.append(("prime gap", "what is the largest difference between two consecutive prime numbers below 10000", gap))
    x, y = 1, 1
    for _ in range(998):
        x, y = y, (3 * y + 2 * x) % 1000003
    r.append(("recurrence", "define a(1) = 1, a(2) = 1 and a(n) = 3*a(n-1) + 2*a(n-2). what is a(1000) modulo "
              "1000003", y))
    r.append(("gcd sum", "what is the sum of gcd(n, 360) over every integer n from 1 to 360 inclusive",
              sum(math.gcd(n, 360) for n in range(1, 361))))
    zeros = str(math.factorial(1400))          # 1400! is under the 4300-digit int-to-str limit, 2026! is not
    r.append(("trailing zeros", "how many trailing zeros does 1400 factorial have",
              len(zeros) - len(zeros.rstrip("0"))))
    r.append(("isqrt", "what are the first 30 digits of the decimal expansion of the square root of 2, written as "
              "one integer with no decimal point (so it starts 1414)", math.isqrt(2 * 10 ** 58)))
    f500 = str(math.factorial(500))
    r.append(("factorial digit sum", "what is the sum of the digits of 500 factorial", sum(map(int, f500))))
    groups = len({"".join(sorted(w)) for w in WORDS})
    r.append(("anagram groups", f"group these words so that two words are in the same group exactly when one is an "
              f"anagram of the other: {WORDS}. how many groups are there", groups))
    r.append(("change", "in how many ways can 250 be made from coins worth 1, 2, 5, 10, 20, 50, 100 and 200, where "
              "order does not matter and any number of each coin may be used",
              change(250, [1, 2, 5, 10, 20, 50, 100, 200])))
    h1, h2 = sha("lobes")[:32], sha("brain")[:32]
    hd = sum(bin(int(c1, 16) ^ int(c2, 16)).count("1") for c1, c2 in zip(h1, h2))
    r.append(("hamming", "take the first 32 hex characters of the sha256 of 'lobes' and the first 32 hex characters "
              "of the sha256 of 'brain'. what is the hamming distance between those two 128 bit values, that is, how "
              "many bit positions differ", hd))
    rl = rle("aaabbbccccdaaeeeeefffffffgg")
    r.append(("run length", "run-length encode 'aaabbbccccdaaeeeeefffffffgg' by writing each character followed by "
              "the length of its run, with no separators, so 'aab' becomes 'a2b1'. what is the result", rl))
    assert len(r) == 30
    return [{"id": f"tools-{50 + i}", "name": n, "prompt": p, "answer": str(a)}
            for i, (n, p, a) in enumerate(r)]


def multistep_v4():
    """30 chains of 4 to 6 steps, three values each, the v4 half."""
    r = []
    ps = primes(2000)
    gaps = [ps[i + 1] - ps[i] for i in range(len(ps) - 1)]
    r.append(("primes to file", "write every prime below 2000 to primes.txt, one per line, then read the file back "
              "and report how many lines it has, the sum of the numbers in it, and the largest difference between "
              "two consecutive numbers in it", [len(ps), sum(ps), max(gaps)]))
    fs, a_, b_ = [], 1, 1
    while a_ <= 10 ** 12:
        fs.append(a_)
        a_, b_ = b_, a_ + b_
    r.append(("fibonacci file", "with fib(1) = fib(2) = 1, write every fibonacci number up to and including "
              "1000000000000 to fib.txt, one per line, then read it back and report how many numbers there are, "
              "how many of them are even, and the sum of the digits of the largest one",
              [len(fs), sum(1 for f in fs if f % 2 == 0), sum(map(int, str(fs[-1])))]))
    sq = [i * i for i in range(1, 1001)]
    pal = [n for n in sq if str(n) == str(n)[::-1]]
    r.append(("palindromic squares", "square every integer from 1 to 1000, write the squares to squares.txt one per "
              "line, read the file back, keep only the palindromic ones, and report how many there are, the largest "
              "of them, and their sum", [len(pal), max(pal), sum(pal)]))
    p3 = str(3 ** 500)
    top3 = max(set(p3), key=lambda c: (p3.count(c), int(c)))
    r.append(("3^500 digits", "compute 3 to the power 500 exactly, write its decimal digits to big.txt, read the "
              "file back and report how many digits it has, which digit occurs most often (on a tie the larger "
              "digit), and how many times that digit occurs", [len(p3), top3, p3.count(top3)]))
    sundays = [D(2026, 1, 1) + dt.timedelta(i) for i in range(365) if (D(2026, 1, 1) + dt.timedelta(i)).weekday() == 6]
    r.append(("sundays", "list every sunday in the year 2026 in iso format, write them to sundays.txt one per line, "
              "then read the file and report how many there are, the first one, and the last one",
              [len(sundays), sundays[0].isoformat(), sundays[-1].isoformat()]))
    ch = sha("lobes")
    for _ in range(9):
        ch = sha(ch)
    r.append(("hash chain 10", "start with the string 'lobes' and take its sha256 hex digest, then take the sha256 "
              "hex digest of that hex string, and repeat until you have applied sha256 ten times in total. report "
              "the first 12 hex characters of the final digest, how many of its 64 characters are letters, and the "
              "sum of the characters that are decimal digits",
              [ch[:12], sum(c.isalpha() for c in ch), sum(int(c) for c in ch if c.isdigit())]))
    m2 = [[sum(MAT[i][k] * MAT[k][j] for k in range(4)) for j in range(4)] for i in range(4)]
    r.append(("matrix square", f"square the 4x4 matrix with rows {MAT} by matrix multiplication, then report the "
              "trace of the result, its determinant, and the sum of all sixteen of its entries",
              [sum(m2[i][i] for i in range(4)), det(m2), sum(sum(row) for row in m2)]))
    n = 999983
    f = factorise(n * 4)
    r.append(("factorise", f"find the prime factorisation of {n * 4}, then report how many distinct prime factors "
              "it has, the largest of them, and the sum of all its prime factors counted with multiplicity",
              [len(f), max(f), sum(p * e for p, e in f.items())]))
    text = ("a witness that ran a program beats a witness that wrote a number and two witnesses that agree beat "
            "one witness that is certain and nothing beats a witness that ran a program twice")
    w = text.split()
    long = [x for x in w if len(x) >= 5]
    top = max(set(long), key=lambda x: (long.count(x), -long.index(x)))
    r.append(("word chain", f"write this text to w.txt: '{text}'. then read it back and report the number of words, "
              "the most frequent word of five or more letters, and how many words appear exactly once",
              [len(w), top, sum(1 for x in set(w) if w.count(x) == 1)]))
    bal, first = Fraction(5000), None
    for m in range(1, 121):
        bal *= Fraction(10050, 10000)
        if first is None and bal > 7500:
            first = m
    r.append(("monthly interest", "5000 is invested at 0.5 percent per month, compounded monthly for 120 months. "
              "report the final balance rounded to cents, the month number (1 to 120) in which the balance first "
              "exceeds 7500, and the total interest earned rounded to cents",
              [f"{float(bal):.2f}", first, f"{float(bal) - 5000:.2f}"]))
    nums = [int(sha(str(i))[:6], 16) % 1000 for i in range(50)]
    uniq = sorted(set(nums))
    r.append(("derived list", "for each integer i from 0 to 49, take the sha256 hex digest of the decimal string of "
              "i, read its first 6 hex characters as a hexadecimal number, and reduce it modulo 1000. write the 50 "
              "results to n.txt one per line, read them back, and report how many distinct values there are, the "
              "largest value, and the median of the distinct values",
              [len(uniq), max(nums), statistics.median(uniq)]))
    b = bin(int(sha("brain")[:8], 16))[2:]
    r.append(("hex to bits", "take the first 8 hex characters of the sha256 of the string 'brain', read them as a "
              "hexadecimal number, write that number in binary, and report the binary string, how many 1 bits it "
              "has, and the number modulo 97", [b, b.count("1"), int(b, 2) % 97]))
    best = max(range(100000, 110000), key=lambda k: collatz(k)[0])
    r.append(("collatz window", "among the starting numbers from 100000 to 109999, find the one whose collatz "
              "sequence takes the most steps to reach 1 (on a tie the smallest such number). report that number, "
              "its number of steps, and the largest value its sequence reaches",
              [best, collatz(best)[0], collatz(best)[1]]))
    divs = sorted(d for d in range(1, 720721) if 720720 % d == 0)
    r.append(("divisors", "list every positive divisor of 720720, then report how many there are, their sum, and "
              "the largest one that is smaller than 720720", [len(divs), sum(divs), divs[-2]]))
    row = [math.comb(40, k) for k in range(41)]
    r.append(("pascal row", "write out row 40 of pascal's triangle, where row 0 is a single 1, then report how many "
              "entries it has, the largest entry, and the sum of all entries",
              [len(row), max(row), sum(row)]))
    letters = "".join(c for c in PARA if c.isalpha())
    enc = "".join(chr((ord(c) - 97 + 5) % 26 + 97) for c in letters)
    r.append(("caesar chain", f"take this text: '{PARA}'. drop everything that is not a letter, shift every "
              "remaining letter forward by 5 places in the alphabet wrapping from z to a, and report the length of "
              "the result, its first 20 characters, and how many times the letter a appears in it",
              [len(enc), enc[:20], enc.count("a")]))
    vals = [84, 126, 210, 462, 858, 1155]
    lc = math.lcm(*vals)
    r.append(("gcd lcm six", f"for the six numbers {vals}: report their greatest common divisor, their least common "
              "multiple, and the sum of the six quotients you get by dividing that least common multiple by each of "
              "the six numbers in turn", [math.gcd(*vals), lc, sum(lc // v for v in vals)]))
    temps_c = [-40, 0, 12, 37, 100, 212]
    fh = [c * 9 / 5 + 32 for c in temps_c]
    r.append(("temperatures", f"convert each of these celsius temperatures to fahrenheit: {temps_c}. write the "
              "results to temps.txt one per line, read them back, and report the mean fahrenheit value to two "
              "decimals, the highest, and how many of them are above freezing in fahrenheit",
              [f"{statistics.mean(fh):.2f}", max(fh), sum(1 for f_ in fh if f_ > 32)]))
    src = "".join(to_base(i, 2) for i in range(1, 40))
    enc2 = rle(src)
    r.append(("binary rle", "write the binary representations of the integers 1 to 39 one after another with no "
              "separators, then run-length encode that string by writing each character followed by its run length "
              "with no separators. report the length of the original string, the length of the encoded string, and "
              "the longest run length that appears",
              [len(src), len(enc2), max(len(x) for x in re.findall(r"0+|1+", src))]))
    t10 = ["030640615", "043942089"]
    c10 = [(11 - sum((10 - i) * int(c) for i, c in enumerate(s)) % 11) % 11 for s in t10]
    t13 = "978030640615"
    c13 = (10 - sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(t13)) % 10) % 10
    r.append(("isbn batch", f"work out the missing check digit of the isbn-10 beginning {t10[0]}, of the isbn-10 "
              f"beginning {t10[1]} and of the isbn-13 beginning {t13}, then report the three complete numbers in "
              "that order, writing X for an isbn-10 check value of 10",
              [s + ("X" if c == 10 else str(c)) for s, c in zip(t10, c10)] + [t13 + str(c13)]))
    yrs = [1987, 2026, 3888]
    r.append(("roman batch", f"write each of {yrs} as a roman numeral and report the three numerals in that order",
              [roman(y) for y in yrs]))
    kap, seen = 6174, []
    k = 3524
    for _ in range(10):
        d = f"{k:04d}"
        k = int("".join(sorted(d, reverse=True))) - int("".join(sorted(d)))
        seen.append(k)
        if k == kap:
            break
    r.append(("kaprekar", "start from 3524. repeatedly write the four digits in descending order, write them in "
              "ascending order, and subtract the second from the first, padding to four digits with leading zeros. "
              "report how many steps it takes to reach 6174, the value after the first step, and the value after "
              "the second step", [len(seen), seen[0], seen[1]]))
    grid = [[(i * 7 + j * 13 + i * j) % 10 for j in range(12)] for i in range(12)]
    diag = sum(grid[i][i] for i in range(12))
    r.append(("grid", "build a 12 by 12 grid where the entry in row i and column j, both counted from 0, is "
              "(7*i + 13*j + i*j) modulo 10. report the sum of every entry, the sum of the main diagonal, and the "
              "largest row sum", [sum(sum(row) for row in grid), diag, max(sum(row) for row in grid)]))
    txt = PARA.replace(" ", "")
    b64 = base64.b64encode(txt.encode()).decode()
    r.append(("base64 chain", f"take this text: '{PARA}'. remove the spaces, base64 encode the result as ascii, and "
              "report the length of the encoded string, its first 16 characters, and how many '=' padding "
              "characters it ends with", [len(b64), b64[:16], len(b64) - len(b64.rstrip("="))]))
    start, end = D(1970, 1, 1), D(2026, 9, 15)
    days = (end - start).days
    r.append(("epoch", "how many days are there from 1970-01-01 to 2026-09-15 inclusive of the first and exclusive "
              "of the last, how many seconds is that, and what weekday is 2026-09-15",
              [days, days * 86400, end.strftime("%A")]))
    win = [sum(TEMPS[i:i + 3]) / 3 for i in range(len(TEMPS) - 2)]
    r.append(("sliding window", f"for these ten readings {TEMPS}, compute every mean of three consecutive readings "
              "in order. report how many such means there are, the largest one to two decimals, and the index "
              "(counting the first reading as index 0) at which that largest window starts",
              [len(win), f"{max(win):.2f}", win.index(max(win))]))
    per, digits = period(49)
    r.append(("1/49", "the decimal expansion of 1/49 repeats. report the length of the repeating period, the sum of "
              "the digits in one period, and the 100th digit after the decimal point",
              [per, sum(digits), digits[(100 - 1) % per]]))
    sq2 = math.isqrt(7 * 10 ** 40)
    r.append(("sqrt digits", "compute the square root of 7 to 20 decimal places. report the integer part, the first "
              "10 digits after the decimal point as one integer, and the sum of those 10 digits",
              [str(sq2)[0], str(sq2)[1:11], sum(map(int, str(sq2)[1:11]))]))
    counts = {}
    for w_ in WORDS:
        counts.setdefault("".join(sorted(w_)), []).append(w_)
    big = max(counts.values(), key=len)
    r.append(("anagram chain", f"group these words into anagram groups: {WORDS}. report how many groups there are, "
              "the size of the largest group, and how many words are in a group of size 1",
              [len(counts), len(big), sum(1 for g_ in counts.values() if len(g_) == 1)]))
    tri = [n * (n + 1) // 2 for n in range(1, 201)]
    pals = [t for t in tri if str(t) == str(t)[::-1] and t > 9]
    r.append(("triangular", "compute the first 200 triangular numbers starting from 1, write them to tri.txt one "
              "per line, read them back, and report their sum, how many of them above 9 are palindromes, and the "
              "largest such palindrome", [sum(tri), len(pals), max(pals)]))
    assert len(r) == 30
    return [{"id": f"multi-{40 + i}", "name": n, "prompt": p, "answers": [str(a) for a in ans]}
            for i, (n, p, ans) in enumerate(r)]


if __name__ == "__main__":
    here = Path(__file__).parent
    for name, items in (("multistep", multistep() + multistep_v4()), ("tools", tools() + tools_v4())):
        assert len(items) == 60
        (here / f"{name}.jsonl").write_text("".join(json.dumps(it, ensure_ascii=False) + "\n" for it in items), encoding="utf-8")
        print(name, len(items))
