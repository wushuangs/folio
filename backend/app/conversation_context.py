import re

from .text import focus_words


FOLLOW_UP = re.compile(r'它|这个|那个|上述|上面|刚才|继续|还有|具体|第[一二三四五六七八九十\d]+[种项个步]|呢[？?]?')


def select_history(messages, document_ids, max_chars=4800):
    """Keep recent, in-scope turns within the prompt budget."""
    turns = []
    pending = None
    scope = set(document_ids)
    for message in messages:
        if message.role == 'user':
            pending = message
        elif message.role == 'assistant' and pending and message.status == 'complete':
            if set((message.request_context or {}).get('document_ids', [])) == scope:
                turns.append((pending, message))
            pending = None
    selected = []
    used = 0
    for user, assistant in reversed(turns):
        user_text = user.content[:500]
        answer_text = assistant.content[:1100]
        size = len(user_text) + len(answer_text)
        if used + size > max_chars:
            break
        selected.insert(0, {'role': 'assistant', 'content': answer_text})
        selected.insert(0, {'role': 'user', 'content': user_text})
        used += size
    return selected


def rewrite_query(question, history):
    if len(question) > 40 or not FOLLOW_UP.search(question):
        return question
    previous = next((item['content'] for item in reversed(history) if item['role'] == 'user'), '')
    if not previous:
        return question
    topic = previous[:180]
    if FOLLOW_UP.search(topic):
        earlier = next((item['content'] for item in reversed(history[:-2]) if item['role'] == 'user' and not FOLLOW_UP.search(item['content'])), '')
        topic = earlier[:180] or topic
    reference = ''
    ordinal = re.search(r'第([一二三四五六七八九十\d]+)[种项个步]', question)
    assistant = next((item['content'] for item in reversed(history) if item['role'] == 'assistant'), '')
    if ordinal and assistant:
        number = ordinal.group(1)
        digit = {'一': '1', '二': '2', '三': '3', '四': '4', '五': '5', '六': '6', '七': '7', '八': '8', '九': '9', '十': '10'}.get(number, number)
        lines = assistant.splitlines()
        reference = next((line.strip()[:160] for line in lines if re.match(rf'^\s*(?:{re.escape(digit)}[.、）)]|第{re.escape(number)}[种项个步])', line)), '')
    return ' '.join(part for part in [topic, reference, question] if part)


def prompt_context(citations, max_chars=6800):
    selected = []
    remaining = max_chars
    for citation in citations:
        heading = citation['heading']
        location = f"{citation['document_name']}，第 {citation['page']} 页，{heading}"
        prefix = f"[{citation['id']}] {location}\n"
        available = remaining - len(prefix)
        if available < 250:
            break
        content = citation['content'][:min(available, 1600)]
        selected.append(prefix + content)
        remaining -= len(prefix) + len(content) + 2
    return '\n\n'.join(selected)
