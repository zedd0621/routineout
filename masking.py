def mask_name(name):
    """이름 가운데 글자를 * 로 마스킹. 2글자면 뒷글자, 4글자 이상이면 가운데 두 글자 중 하나."""
    if not name:
        return name
    n = len(name)
    if n <= 1:
        return name
    if n == 2:
        return name[0] + '*'
    mid = n // 2
    return name[:mid] + '*' + name[mid + 1:]


def mask_birth(birth):
    """생년월일에서 숫자만 추출해 앞 1자리만 남기고 나머지는 * 처리.
    예: '1988-06-02' / '19880602' -> '8*******'"""
    if not birth:
        return birth
    digits = ''.join(c for c in str(birth) if c.isdigit())
    if not digits:
        return birth
    return digits[0] + '*' * (len(digits) - 1)


def mask_phone(tel):
    """전화번호 010-XXXX-XXXX 형태에서 앞 2자리만 남기고 마스킹.
    예: '010-5588-1234' -> '010-55**-****'"""
    if not tel:
        return tel
    digits = ''.join(c for c in str(tel) if c.isdigit())
    if len(digits) != 11:
        return tel
    return f'{digits[:3]}-{digits[3:5]}**-****'
