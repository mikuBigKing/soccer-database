"""敏感字段脱敏，输出格式与契约文档示例一致。"""


def mask_id_card(value: str) -> str:
    """'522105199001011234' → '522***XXXX****1234'。"""
    if not value:
        return ''
    if len(value) <= 7:
        return '*' * len(value)
    return f'{value[:3]}***XXXX****{value[-4:]}'


def mask_phone(value: str) -> str:
    """'13800005678' → '138****5678'。"""
    if not value:
        return ''
    if len(value) <= 7:
        return '*' * len(value)
    return f'{value[:3]}****{value[-4:]}'
