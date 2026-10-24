#!/usr/bin/env python3
"""日本語の文章を機械で見る。拾えるのは形のあるものだけ。意味と流れは人（AI）が読み直す。

  proofread.py <file>...            指定したファイル
  proofread.py --diff [<range>]     差分の追加行（省略時は未コミットの変更）
  proofread.py --tree               追跡している .md .txt .rst
  proofread.py --stdin              標準入力の文（会話の返事、コミットメッセージ、PR の本文）
  --strict                          注意でも終了コード 1 にする
  --no-textlint                     textlint を使わない（入っていれば自動で使う。scripts/setup-textlint.sh）
  --no-ai                           AI っぽさの目印（scripts/ai_habits.py）を数えない
  --doc-type business|article|manual  AI っぽさのしきい値（既定 business）

重大: 外国語の字の混入、助詞の重複、英語の出だし。見た目が壊れて見えるもの。終了コード 1
注意: ら抜き、二重敬語、翻訳調、勝手な訳語、句読点、長い文、文体の混在。読む人が引っかかるもの
表記: 開く漢字、送り仮名、ゆれ。まとめて数える。決まりは references/rules.md と、そのリポジトリの CLAUDE.md
コードブロック、表の行、`コード`、URL は見ない。行末に `ja-proofread: ignore` があればその行も見ない。
"""
import argparse, json, os, re, subprocess, sys, tempfile
from collections import defaultdict
from pathlib import Path

JA = r"ぁ-んァ-ヶ一-龥々ー"
HAS_JA = re.compile(f"[{JA}]")

# 日本語の文書に混ざると壊れて見える字。簡体字は日本語に無い字形だけを選んである（国・学・会・体・点は日本語）
SIMPLIFIED = "们这个说对时间为么还让过发经现样种开关东车书长实认语请问题应该从产业务变电网页软库户门见边达进运选择错误识别处办动员图团际维结构组级线统续响调设计试验质态标签"
TRADITIONAL = "們這對說體國學點實關寫讀發經樣當變數據戶邊擇處辦圖團續驗簽歡歷鐵權應從產與參齊豐廣會來"

# (名前, 正規表現, 深刻度, 直し方)
RULES = [
    ("ハングルの混入",   r"[가-힯ᄀ-ᇿ㄰-㆏]+", "high", "日本語の字に"),
    ("中国語の字の混入", f"[{SIMPLIFIED}]+", "high", "日本語の字に（簡体字）"),
    ("旧字体・繁体字",   f"[{TRADITIONAL}]+", "medium", "日本語の字に。固有名詞なら残す"),
    ("助詞の重複",       r"(?<!も)のの(?![ぁ-ん])|をを|にに(?![ぁ-ん])|がが|(?<!ま)でで(?![ぁ-ん])", "high", "1つにする"),
    ("英語の出だし",     r"^\s*(?:Now|Let me|Let's|First|Sure|Okay|OK|Here(?:'s| is)|I'll|I've|I am|Great|Certainly|Alright)\b", "high", "日本語で書き出す"),
    ("強調の記号",       r"\*\*[^*\n]+\*\*", "medium", "記号で強調しない。見出しか文で示す"),
    ("全角の英数字",     r"[Ａ-Ｚａ-ｚ０-９]+", "medium", "半角に"),
    ("半角カナ",         r"[ｦ-ﾟ]+", "medium", "全角に"),
    ("和文の中の半角句読点", f"[{JA}][,.](?=[{JA}\\s]|$)", "medium", "、。に"),
    ("二重敬語",         r"おっしゃられ|ご覧になられ|拝見させていただ|お伺いさせていただ|お越しになられ|ご確認させていただ", "medium", "敬語を1つにする"),
    ("過剰な敬語",       r"させていただ|させて頂", "medium", "「する」「します」で足りる"),
    ("ら抜き",           r"(?:見|来|食べ|出|着|起き|寝|決め|信じ|考え|覚え|答え|受け|教え|変え|止め|生き|降り|借り|開け|閉め|調べ|集め|比べ|伝え|並べ|投げ|逃げ|乗せ|載せ|やめ|辞め|立て|育て|捨て|届け|分け|避け|逃れ)れ(?=る|ます|ない|た[。、\s]|て)", "medium", "「られる」に"),
    ("翻訳調",           r"することができ(?:る|ます|ない|ません)", "medium", "「できる」に"),
    ("翻訳調",           r"[一-龥]{2,}を行(?:う|い|います|った|って|わ)", "medium", "「〜する」に"),
    ("翻訳調",           r"という(?:こと|もの)(?:が|を|は|に|で)", "medium", "「という〜」を削る"),
    ("翻訳調",           r"(?:に|と)(?:関して|関する|おいて|おける)(?![ぁ-ん])", "medium", "「で」「の」に"),
    ("翻訳調",           r"(?:非常に|極めて|大変)(?=[ぁ-ん一-龥])", "medium", "数字か事実に置き換えるか、削る"),
    ("翻訳調",           r"を可能に(?:し|する|なり)|することが可能(?:です|だ|である|にな)", "medium", "「〜できる」「〜できるようにする」に"),
    ("翻訳調",           r"する必要があ(?:る|り)|ことが重要(?:です|だ|である)", "medium", "「〜する」「〜してください」に。理由は1文"),
    ("翻訳調",           r"と(?:思われ|考えられ)(?:ます|る)|ように見え(?:ます|る)", "medium", "事実か推測かを書く。推測なら「確かめていない」"),
    ("翻訳調",           r"(?:することを|することが|ことを|ことが)(?:推奨|お勧め|おすすめ)", "medium", "「〜してください」に"),
    ("翻訳調",           r"(?:基本的に|原則として)(?=[、,])", "medium", "例外を書かないなら削る"),
    ("矢印でつないだ羅列", r"→[^→\n]*→", "medium", "文にする。「AをBに変えて、Cになった」（references/translationese.md）"),
]

# 勝手な訳語。開発者の間で通る語に戻す。対応表は references/glossary.md。(正規表現, 使う語)
GLOSSARY = [
    (r"作業枝", "作業ブランチ"), (r"本線", "main ブランチ"), (r"枝(?![葉分])", "ブランチ"),
    (r"既定ブランチ", "デフォルトブランチ"), (r"既定", "デフォルト"),
    (r"題目", "トピック"), (r"(?<=の)題(?=[はをがと、。\s]|$)|題名", "タイトル"),
    (r"(?<![専部正])門(?![題戸])", "ゲート"), (r"目印", "マーカー"), (r"(?<![目矢])印(?![刷鑑象字影])", "マーク"),
    (r"容器", "コンテナ"), (r"中継", "プロキシ"), (r"走査", "スキャン"), (r"囲み", "コードブロック"),
    (r"字下げ", "インデント"), (r"試験(?![官勉])", "テスト"), (r"検品", "チェック"), (r"(?<![丸書き])写し(?![てたまょ])", "コピー"),
    (r"凍結", "アーカイブ"), (r"利用枠|枠切れ", "レート制限"), (r"結びつけ", "紐づけ"), (r"起床", "実行"),
    (r"配備", "デプロイ"), (r"利用者", "ユーザー"), (r"束ねる側", "親セッション"), (r"道具", "ツール"),
    (r"和文", "日本語"), (r"手本", "参考画像"), (r"回N", "N回目"), (r"周N", "N周目"),
    (r"(?<![0-9N一-龥])周(?![期辺囲回年目波])", "サイクル"), (r"毎周", "毎サイクル"),
]

# 表記。(直し方, 正規表現)。まとめて数える。決まりはリポジトリのものが優先
STYLE = [
    ("出来る→できる",     r"出来(?:る|ま|な|れ|た|て)"),
    ("下さい→ください",   r"[てで]下さい"),
    ("頂く→いただく",     r"[てで]頂(?:き|く|け|い)"),
    ("致す→いたす",       r"[いきしちにひみりえけせてねへめれ]致(?:し|す)"),
    ("有難う→ありがとう", r"有難う|有り難う"),
    ("宜しく→よろしく",   r"宜しく"),
    ("事→こと",           r"[るいたなの]事(?=[がをはにもでだ、。])"),
    ("時→とき",           r"[るいた]時(?=[はにも、。])"),
    ("為→ため",           r"[るいたなの]為(?=[にの、。])"),
    ("行なう→行う",       r"行な(?:う|い|っ|わ)"),
    ("漢字を開く",        r"(?<![一-龥])(?:及び|又は|並びに|若しくは|更に|既に|全て|殆ど|様々|是非|予め|概ね|且つ|但し|即ち|勿論|沢山|丁度|一旦|滅多|流石|所謂|御座い|迄)(?![一-龥])"),
]

# 表記ゆれ。同じ文書に2つ以上の形があるときだけ出す
VARIANTS = [
    ("サーバ", "サーバー"), ("ユーザ", "ユーザー"), ("コンピュータ", "コンピューター"), ("フォルダ", "フォルダー"),
    ("ブラウザ", "ブラウザー"), ("プリンタ", "プリンター"), ("メモリ", "メモリー"), ("エディタ", "エディター"),
    ("インタフェース", "インターフェース", "インターフェイス"), ("パラメータ", "パラメーター"),
    ("問い合わせ", "問合せ", "問合わせ"), ("取り扱い", "取扱い", "取扱"), ("申し込み", "申込み", "申込"),
    ("打ち合わせ", "打合せ"), ("組み合わせ", "組合せ"), ("立ち上げ", "立上げ"), ("引っ越し", "引越し"),
    ("できる", "出来る"), ("すべて", "全て"), ("すでに", "既に"), ("さらに", "更に"), ("ため", "為"),
    ("ください", "下さい"), ("いただ", "頂"), ("および", "及び"), ("または", "又は"),
]

FENCE = re.compile(r"^\s*(```|~~~)")
CODE_SPAN = re.compile(r"`[^`\n]*`")
URL = re.compile(r"https?://\S+|[\w./-]+\.(?:md|py|sh|json|yml|yaml|txt)\b")
PROSE_SUFFIX = {".md", ".txt", ".rst", ".markdown"}
IGNORE_MARK = "ja-proofread: ignore"   # 字の一覧のような、意図して置いた行に付ける
LONG = 120    # 1文の目安（字）
COMMAS = 5    # 1文の読点の目安


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace").stdout


# ---- textlint（入っていれば使う） ---------------------------------------------
# 形態素解析で見るので、ら抜き・助詞の重複・冗長表現・弱い表現を正規表現より正しく拾う。
# 置き場所はリポジトリの外（scripts/setup-textlint.sh）。無ければ黙って正規表現だけで動く。
def textlint_bin():
    d = Path(os.environ.get("CIEL_TEXTLINT_DIR") or Path.home() / ".cache/ciel-textlint")
    b = d / "node_modules/.bin/textlint"
    return (b, d / ".textlintrc.json") if b.exists() else (None, None)


# ---- AI っぽさ（natural-japanese-writing の check.py） ------------------------
# 文書ごとに、太字・ダッシュ・否定対比・頻出語・語尾の偏り・文の長さの揃いすぎを数える。判定ではなく目印。
def ai_habits(files, doc_type="business"):
    script = Path(__file__).resolve().parent / "ai_habits.py"
    out = []
    for name, p in files:
        try:
            r = subprocess.run([sys.executable, "-I", str(script), str(p), "--json", "--type", doc_type],
                               capture_output=True, text=True, errors="replace", timeout=60)
            d = json.loads(r.stdout or "{}")
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            continue
        star = d.get("star_total", 0)
        flags = d.get("flags", [])
        dense = d.get("dense_paragraphs", [])
        if star or flags or dense:
            words = "、".join(f"{k.lstrip('★')} {v['count']}" for k, v in d.get("words", {}).items() if k.startswith("★"))
            marks = "、".join(f"{k.lstrip('★')} {v}" for k, v in d.get("marks", {}).items() if k.startswith("★"))
            out.append((name, star, flags, [f"段落{x['paragraph']}（{x['hits']}種）" for x in dense], "、".join(x for x in (words, marks) if x)))
    return out


def run_textlint(files, only_lines=None):
    """files: [(表示名, 実体のパス)]。only_lines: {表示名: {行番号}} で差分の行だけに絞る。
    返り値は (sev, rule, loc, hit, fix) の列。"""
    b, cfg = textlint_bin()
    if not b:
        return [], False
    out = []
    try:
        r = subprocess.run([str(b), "--config", str(cfg), "--format", "json", *[str(p) for _, p in files]],
                           capture_output=True, text=True, errors="replace", timeout=180)
        data = json.loads(r.stdout or "[]")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return [], True
    names = {str(p): n for n, p in files}
    for item in data:
        name = names.get(item.get("filePath", ""), item.get("filePath", ""))
        for m in item.get("messages", []):
            ln = m.get("line", 0)
            if only_lines is not None and ln not in only_lines.get(name, set()):
                continue
            rule = (m.get("ruleId") or "").split("/")[-1]
            text = (m.get("message") or "").splitlines()[0][:60]
            out.append(("medium", f"textlint {rule}", f"{name}:{ln}", text, "textlint の指摘"))
    return out, True


class Proof:
    def __init__(self):
        self.findings = []            # (sev, name, loc, hit, fix)
        self.style = defaultdict(list)   # fix -> [loc]
        self.variants = defaultdict(lambda: defaultdict(list))  # group -> form -> [loc]
        self.spacing = {"空けない": [], "空ける": []}
        self.parens = {"半角": [], "全角": []}
        self.tone = defaultdict(lambda: {"敬体": 0, "常体": 0})
        self.lines = 0
        self.textlint = False
        self.habits = []

    # 1文書
    def document(self, name, text):
        in_fence = False
        in_front = text.startswith("---\n")
        for i, raw in enumerate(text.splitlines(), 1):
            if FENCE.match(raw):
                in_fence = not in_fence
                continue
            if in_front:
                if i > 1 and raw.strip() == "---":
                    in_front = False
                # frontmatter は description だけ見る。長さは数えない
                if not raw.startswith("description:"):
                    continue
            if in_fence or raw.lstrip().startswith("|") or raw.lstrip().startswith("    "):
                continue
            line = URL.sub(" ", CODE_SPAN.sub(" ", raw))
            loc = f"{name}:{i}"
            self.lines += 1
            self.line(line, loc, measure=not in_front)

    def line(self, line, loc, measure=True):
        if IGNORE_MARK in line:
            return
        ja = bool(HAS_JA.search(line))
        for rule, pat, sev, fix in RULES:
            if rule == "英語の出だし" and not ja:
                continue  # 英語だけの文書は対象外
            if rule in ("和文の中の半角句読点", "ら抜き", "二重敬語", "過剰な敬語", "翻訳調", "助詞の重複") and not ja:
                continue
            for m in re.finditer(pat, line):
                self.findings.append((sev, rule, loc, m.group(0), fix))
        if not ja:
            return
        for pat, word in GLOSSARY:
            for m in re.finditer(pat, line):
                self.findings.append(("medium", "勝手な訳語", loc, m.group(0), f"「{word}」に（references/glossary.md）"))
        for fix, pat in STYLE:
            for m in re.finditer(pat, line):
                self.style[fix].append(f"{loc} {m.group(0)}")
        for group in VARIANTS:
            for form in group:
                # 「ユーザ」は「ユーザー」の一部でもあるので、長音が続くものは短い形に数えない
                if re.search(re.escape(form) + "(?!ー)", line):
                    self.variants[group[0]][form].append(loc)
        if re.search(f"[{JA}][A-Za-z0-9]", line):
            self.spacing["空けない"].append(loc)
        if re.search(f"[{JA}] [A-Za-z0-9]", line):
            self.spacing["空ける"].append(loc)
        if re.search(f"[{JA}]\\(", line):
            self.parens["半角"].append(loc)
        if "（" in line:
            self.parens["全角"].append(loc)
        if not measure:
            return
        doc = loc.rsplit(":", 1)[0]
        sents = [x.strip() for x in re.split(r"(?<=[。！？])", line) if x.strip()]
        # 体言止めが3つ続く行（「検査は通過。マージは完了。次は配布。」）は電報に見える
        nounstop = [x for x in sents if re.search(f"[一-龥ァ-ヶー][。！？]$", x) and not re.search(r"(?:する|した|いる|ある|です|ます|ない|れる|せる|った|んだ)[。！？]$", x)]
        if len(sents) >= 3 and len(nounstop) >= 3:
            self.findings.append(("medium", "体言止めの連発", loc, "。".join(x[:8] for x in nounstop[:3]) + "…", "述語のある文にする（references/translationese.md）"))
        # 同じ文末が3つ続く（「〜ました。〜ました。〜ました。」）
        ends = [x[-3:] for x in sents if len(x) >= 3]
        if len(ends) >= 3 and any(ends[i] == ends[i + 1] == ends[i + 2] for i in range(len(ends) - 2)):
            self.findings.append(("medium", "文末の繰り返し", loc, "…" + ends[0], "文をつなぐか、言い方を変える"))
        for sent in re.split(r"(?<=[。！？])", line):
            s = sent.strip()
            if not s:
                continue
            if re.search(r"(?:です|ます|ません|でした|ました|ましょう|ください)[。！？]?$", s):
                self.tone[doc]["敬体"] += 1
            elif re.search(f"[{JA}](?:だ|である|だった|た|ない|[うくぐすつぬぶむるい])[。！？]$", s):
                self.tone[doc]["常体"] += 1
            body = re.sub(r"\s", "", s)
            if len(body) > LONG:
                self.findings.append(("medium", "長い文", loc, f"{len(body)} 字", "2つに分ける。一文一義"))
            parts = s.split("、")
            # 短い語の列挙（A、B、C、…）は読点が多くても読める。平均 8 字未満なら列挙とみなす
            if len(parts) > COMMAS and sum(len(x) for x in parts) / len(parts) >= 8:
                self.findings.append(("medium", "読点が多い", loc, f"、が {len(parts) - 1} 個", "文を分けるか、読点を減らす"))

    def report(self, label, strict):
        print(f"== ja-proofread: {label} ==")
        order = {"high": 0, "medium": 1}
        mark = {"high": "!!", "medium": " ?"}
        jp = {"high": "重大", "medium": "注意"}
        for sev, rule, loc, hit, fix in sorted(self.findings, key=lambda f: (order[f[0]], f[1], f[2])):
            print(f"  {mark[sev]} [{jp[sev]}] {rule}\n       {loc}  {hit[:60]}  → {fix}")
        n_style = 0
        for fix, locs in sorted(self.style.items(), key=lambda x: -len(x[1])):
            n_style += len(locs)
            head = "、".join(locs[:3]) + ("…" if len(locs) > 3 else "")
            print(f"  -- [表記] {fix} {len(locs)} 件（{head}）")
        for group, forms in self.variants.items():
            if len(forms) < 2:
                continue
            n_style += 1
            desc = " / ".join(f"{f} {len(l)}" for f, l in sorted(forms.items(), key=lambda x: -len(x[1])))
            minor = min(forms.items(), key=lambda x: len(x[1]))
            print(f"  -- [表記] ゆれ: {desc}。少ない方 {minor[0]} は {', '.join(minor[1][:3])}")
        for name, d in (("和文と英数字の間の空白", self.spacing), ("括弧", self.parens)):
            a, b = (len(v) for v in d.values())
            if min(a, b) >= 3 and min(a, b) / (a + b) >= 0.2:
                n_style += 1
                k_minor = min(d, key=lambda k: len(d[k]))
                print(f"  -- [表記] {name}のゆれ: " + " / ".join(f"{k} {len(v)}" for k, v in d.items())
                      + f"。少ない方 {k_minor} は {', '.join(d[k_minor][:3])}")
        for doc, t in self.tone.items():
            a, b = t["敬体"], t["常体"]
            if min(a, b) >= 3 and min(a, b) / (a + b) >= 0.2:
                self.findings.append(("medium", "文体の混在", doc, f"敬体 {a} / 常体 {b}", "どちらかに揃える"))
                print(f"   ? [注意] 文体の混在\n       {doc}  敬体 {a} / 常体 {b}  → どちらかに揃える")
        high = sum(1 for f in self.findings if f[0] == "high")
        med = sum(1 for f in self.findings if f[0] == "medium")
        print(f"\n  重大 {high} / 注意 {med} / 表記 {n_style}（{self.lines} 行）")
        for name, star, flags, dense, detail in self.habits:
            print(f"  -- [AI っぽさ] {name}: ★ {star}" + (f"（{detail}）" if detail else "") + ("。" + "。".join(flags) if flags else "")
                  + (f"。重なった段落: {', '.join(dense)}" if dense else "") + "。natural-japanese-writing の check.py")
        if not self.textlint:
            print("  textlint は入っていない（scripts/setup-textlint.sh で入る。形態素解析の規則が増える）")
        if high + med + n_style == 0:
            print("  このルールでは引っかからなかった。意味と流れは references/rules.md の観点で読み直す")
        return 1 if high or (strict and (med or n_style)) else 0


def line_context(path):
    """作業ツリーのファイルから、行ごとの扱いを決める。差分の行番号は新しい側なので、そのまま引ける。
    skip=囲み・表・字下げ、front=frontmatter（長さを数えない）、それ以外は None。"""
    ctx = {}
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return ctx
    in_fence, in_front = False, text.startswith("---\n")
    for i, raw in enumerate(text.splitlines(), 1):
        if FENCE.match(raw):
            in_fence = not in_fence
            ctx[i] = "skip"
            continue
        if in_front:
            if i > 1 and raw.strip() == "---":
                in_front = False
            ctx[i] = "front" if raw.startswith("description:") else "skip"
            continue
        if in_fence or raw.lstrip().startswith("|") or raw.lstrip().startswith("    "):
            ctx[i] = "skip"
    return ctx


def iter_diff(rng):
    out = run(["git", "diff", "--unified=0"] + ([rng] if rng else ["HEAD"]))
    docs, path, ln = defaultdict(list), "?", 0
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            ln = int(m.group(1)) if m else 0
        elif line.startswith("+") and not line.startswith("+++"):
            if Path(path).suffix.lower() in PROSE_SUFFIX or path in ("CLAUDE.md",):
                docs[path].append((ln, line[1:]))
            ln += 1
    return docs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--diff", nargs="?", const="", metavar="RANGE")
    ap.add_argument("--tree", action="store_true")
    ap.add_argument("--stdin", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--no-textlint", action="store_true")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--doc-type", choices=["business", "article", "manual"], default="business")
    a = ap.parse_args()
    p = Proof()
    tl_files, tl_lines = [], None
    if a.stdin:
        text = sys.stdin.read()
        p.document("入力", text)
        label = "標準入力"
        tmp = Path(tempfile.mkdtemp()) / "input.md"
        tmp.write_text(text)
        tl_files = [("入力", tmp)]
    elif a.diff is not None:
        tl_lines = {}
        for path, lines in iter_diff(a.diff or None).items():
            ctx = line_context(path)
            if Path(path).exists():
                tl_files.append((path, Path(path)))
            for ln, text in lines:
                kind = ctx.get(ln)
                if kind == "skip":
                    continue
                tl_lines.setdefault(path, set()).add(ln)
                p.lines += 1
                p.line(URL.sub(" ", CODE_SPAN.sub(" ", text)), f"{path}:{ln}", measure=kind != "front")
        label = f"差分 {a.diff or 'HEAD'}"
    else:
        files = a.files or [f for f in run(["git", "ls-files"]).splitlines() if Path(f).suffix.lower() in PROSE_SUFFIX]
        for f in files:
            try:
                p.document(f, Path(f).read_text(errors="replace"))
            except (OSError, IsADirectoryError):
                continue
            tl_files.append((f, Path(f)))
        label = f"{len(files)} ファイル"
    if tl_files and not a.no_textlint:
        found, p.textlint = run_textlint(tl_files, tl_lines)
        p.findings.extend(found)
    if tl_files and not a.no_ai:
        p.habits = ai_habits(tl_files, a.doc_type)
    return p.report(label, a.strict)


if __name__ == "__main__":
    sys.exit(main())
