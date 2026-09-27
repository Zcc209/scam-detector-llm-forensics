"""Local LLM (Ollama) as a structured evidence extractor, not as the final judge.

The LLM labels the speech act (is the text soliciting the reader, or discussing / reporting?) and
lists scam tactics, each with a verbatim quote. Quotes that do not occur in the input are dropped
as hallucinations, so every counted tactic is checkable against the original text.
Runs fully offline against http://127.0.0.1:11434; if Ollama is not running the module reports
`unavailable` and the rest of the pipeline continues.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata
import urllib.error
import urllib.request

HOST = os.environ.get('OLLAMA_HOST', 'http://127.0.0.1:11434')
MODEL = os.environ.get('ANTIFRAUD_LLM', 'qwen2.5:7b')
CACHE = Path(__file__).resolve().parent / 'artifacts' / 'llm_cache'
PROMPT_VERSION = 'v2'
TACTICS = ['guaranteed_return', 'off_platform_contact', 'investment_group', 'impersonation', 'urgency',
           'upfront_payment', 'crypto_transfer', 'job_lure', 'romance_lure', 'prize_or_giveaway',
           'account_phishing', 'threat_or_extortion', 'charity_request']
SPEECH_ACTS = ['solicitation', 'advertisement', 'discussion', 'news_or_info', 'personal_share', 'ui_or_navigation', 'other']
SCHEMA = {
    'type': 'object',
    'properties': {
        'speech_act': {'type': 'string', 'enum': SPEECH_ACTS},
        'addresses_reader': {'type': 'boolean'},
        'tactics': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'tactic': {'type': 'string', 'enum': TACTICS}, 'quote': {'type': 'string'}},
            'required': ['tactic', 'quote']}},
        'risk': {'type': 'string', 'enum': ['low', 'medium', 'high']},
        'rationale': {'type': 'string'},
    },
    'required': ['speech_act', 'addresses_reader', 'tactics', 'risk', 'rationale'],
}
SYSTEM = """你是台灣防詐分析助理。你只負責「抽取證據」，不直接判定帳號是否為詐騙。
請閱讀一段社群貼文、網頁或截圖 OCR 文字，輸出 JSON：
1. speech_act：這段文字的主要言語行為。
   - solicitation：要求讀者採取行動（加好友、私訊、點連結、匯款、投資、填資料）。
   - advertisement：一般商品或服務廣告，有明確商家與正常購買流程。
   - discussion：討論、提問、分享看法、論壇標題或熱門話題，即使提到薪水、投資、測驗、感情等字眼。
   - news_or_info：新聞、公告、防詐宣導、知識說明。
   - personal_share：個人生活分享。
   - ui_or_navigation：網站介面、按鈕、選單文字。
2. addresses_reader：是否直接對讀者喊話並要求行動。
3. tactics：只列出文字中「確實出現」的詐騙手法，quote 必須逐字複製原文片段（10~40 字），不可改寫。沒有就給空陣列。
   手法定義：guaranteed_return 保證獲利/高報酬；off_platform_contact 引導到 LINE/Telegram 等站外私聊；
   investment_group 投資群組/老師帶單；impersonation 冒充名人、官方、機構或客服；urgency 限時、名額、立即；
   upfront_payment 先付費、保證金、手續費；crypto_transfer 虛擬貨幣或匯款指示；job_lure 輕鬆高薪兼職；
   romance_lure 以感情取得信任；prize_or_giveaway 中獎、免費領取；account_phishing 要求登入、驗證、輸入帳密；
   threat_or_extortion 威脅勒索；charity_request 可疑募款。
4. risk：只根據你列出的證據給 low/medium/high。單純提到敏感字詞、討論詐騙、新聞報導，應為 low。
5. rationale：30 字以內繁體中文理由。
OCR 文字可能有錯字與斷行，請依語意判讀，但 quote 仍須逐字取自原文。

台灣常見詐騙話術（出現時 speech_act 應為 solicitation，並列出對應手法）：
- 假買家／假賣貨便／假物流：要求對方點連結「填寫運費、簽署、認證、獲取單據、收款、解除凍結」→ account_phishing 或 upfront_payment。
- 假客服／假機關：帳戶異常、分期設定錯誤、監管帳戶、包裹或帳單待繳 → impersonation 或 account_phishing。
- 假投資：老師帶單、飆股群組、保證獲利、內線消息 → investment_group、guaranteed_return。
- 假交友：以感情建立信任後談投資、借錢或匯款 → romance_lure。
- 假中獎、免費領取、限時名額，要求點連結或加好友 → prize_or_giveaway、urgency。
即使語氣像朋友聊天或個人分享，只要要求對方點連結、填資料、付款或加 LINE，就不是 personal_share。"""


def _norm(value):
    # Punctuation and spacing are ignored: OCR inserts stray quotes/commas that the LLM (rightly) drops.
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', value or '')).lower()


def available(timeout=2):
    try:
        with urllib.request.urlopen(HOST + '/api/tags', timeout=timeout) as response:
            names = [m.get('name', '') for m in json.load(response).get('models', [])]
        return any(name == MODEL or name.startswith(MODEL + ':') or name.split(':')[0] == MODEL for name in names)
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _chat(text, timeout):
    body = json.dumps({'model': MODEL, 'stream': False, 'format': SCHEMA,
                       'options': {'temperature': 0, 'seed': 7, 'num_ctx': 4096},
                       'messages': [{'role': 'system', 'content': SYSTEM},
                                    {'role': 'user', 'content': '待分析文字：\n"""\n' + text + '\n"""'}]}).encode('utf-8')
    request = urllib.request.Request(HOST + '/api/chat', data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(json.load(response)['message']['content'])


def verify(raw, text):
    source = _norm(text)
    verified, rejected = [], []
    for item in raw.get('tactics') or []:
        quote = str(item.get('quote', '')).strip()
        ok = item.get('tactic') in TACTICS and len(_norm(quote)) >= 2 and _norm(quote) in source
        (verified if ok else rejected).append({'tactic': item.get('tactic'), 'quote': quote})
    unique = {item['tactic'] for item in verified}
    speech = raw.get('speech_act') if raw.get('speech_act') in SPEECH_ACTS else 'other'
    risk = raw.get('risk') if raw.get('risk') in ('low', 'medium', 'high') else 'low'
    return {'status': 'SUCCESS', 'model': MODEL, 'prompt_version': PROMPT_VERSION,
            'speech_act': speech, 'addresses_reader': bool(raw.get('addresses_reader')),
            'tactics': verified, 'rejected_quotes': rejected, 'risk': risk,
            'rationale': str(raw.get('rationale', ''))[:120],
            'features': {'llm_tactic_count': len(unique), 'llm_solicitation': int(speech == 'solicitation'),
                         'llm_addresses_reader': int(bool(raw.get('addresses_reader'))),
                         'llm_risk': {'low': 0, 'medium': 1, 'high': 2}[risk],
                         'llm_benign_act': int(speech in ('discussion', 'news_or_info', 'personal_share', 'ui_or_navigation'))},
            'note': 'LLM output is extracted evidence; only quotes found verbatim in the input are counted.'}


def analyze(text, timeout=120, use_cache=True):
    text = (text or '').strip()[:3000]
    if len(_norm(text)) < 8:
        return {'status': 'SKIPPED_TEXT_EMPTY'}
    key = hashlib.sha256(f'{MODEL}|{PROMPT_VERSION}|{text}'.encode('utf-8')).hexdigest()
    path = CACHE / f'{key}.json'
    if use_cache and path.is_file():
        return json.loads(path.read_text(encoding='utf-8'))
    try:
        raw = _chat(text, timeout)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        return {'status': 'unavailable', 'model': MODEL,
                'error': f'{type(exc).__name__}: {exc}', 'hint': f'Start Ollama and run: ollama pull {MODEL}'}
    result = verify(raw, text)
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('text')
    args = parser.parse_args()
    print(json.dumps(analyze(args.text, use_cache=False), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
