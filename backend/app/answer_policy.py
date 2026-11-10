import re


def validate_response(answer, citations):
    refusal=re.match(r'^\s*###\s*(资料不足|需要澄清)',answer)
    if refusal:
        # 拒答只保留原因与澄清，不夹带操作建议或参考答案。
        first=re.split(r'\n###\s+',answer.strip(),maxsplit=1)[0]
        return first, [], 'clarification' if refusal.group(1)=='需要澄清' else 'no_evidence', ''
    paragraphs=[part.strip() for part in re.split(r'\n\s*\n',answer) if part.strip()]
    unsupported=[]
    for part in paragraphs:
        content='\n'.join(line for line in part.splitlines() if not line.lstrip().startswith('#'))
        if len(content)>35 and not re.search(r'\[\d+\]',content):unsupported.append(content)
    if unsupported:
        return '### 资料不足\n\n生成内容存在缺少来源的实质性说明，本次未采纳。请查看原文或缩小问题范围。', [], 'no_evidence', '回答来源覆盖检查未通过；请核对原文。'
    return None
