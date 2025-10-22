"""
手机号处理工具
用于处理Telegram用户手机号的国际格式转换
"""

import re
from typing import Optional, Tuple


def detect_country_code(phone_number: str) -> Optional[str]:
    """
    根据手机号长度和格式尝试检测国家区号
    这是一个启发式方法，不是100%准确
    """
    phone = phone_number.strip()
    
    # 常见国家区号映射（基于号码长度和前缀）
    country_patterns = {
        # 中国 +86
        r'^1[3-9]\d{9}$': '+86',
        # 美国/加拿大 +1
        r'^[2-9]\d{2}[2-9]\d{2}\d{4}$': '+1',
        # 菲律宾 +63
        r'^9\d{9}$': '+63',
        # 越南 +84
        r'^[3-9]\d{8}$': '+84',
        # 泰国 +66
        r'^[6-9]\d{8}$': '+66',
        # 马来西亚 +60
        r'^1[0-9]\d{7,8}$': '+60',
        # 新加坡 +65
        r'^[89]\d{7}$': '+65',
        # 印度尼西亚 +62
        r'^[2-9]\d{8,10}$': '+62',
        # 印度 +91
        r'^[6-9]\d{9}$': '+91',
        # 俄罗斯 +7
        r'^9\d{9}$': '+7',
        # 英国 +44
        r'^7[4-9]\d{8}$': '+44',
        # 德国 +49
        r'^1[5-7]\d{8,9}$': '+49',
        # 法国 +33
        r'^[67]\d{8}$': '+33',
        # 日本 +81
        r'^[789]\d{8}$': '+81',
        # 韩国 +82
        r'^1[0-9]\d{7,8}$': '+82',
    }
    
    for pattern, country_code in country_patterns.items():
        if re.match(pattern, phone):
            return country_code
    
    return None


def format_phone_number(phone_number: str, force_country_code: Optional[str] = None) -> str:
    """
    格式化手机号为国际标准格式
    
    Args:
        phone_number: 原始手机号
        force_country_code: 强制使用的国家区号
    
    Returns:
        格式化后的国际手机号
    """
    phone = phone_number.strip()
    
    # 如果已经有+号，直接返回
    if phone.startswith('+'):
        return phone
    
    # 如果强制指定了国家区号
    if force_country_code:
        if not force_country_code.startswith('+'):
            force_country_code = '+' + force_country_code
        return force_country_code + phone
    
    # 尝试自动检测国家区号
    detected_code = detect_country_code(phone)
    if detected_code:
        return detected_code + phone
    
    # 如果无法检测，默认添加+号（让用户手动处理）
    return '+' + phone


def is_valid_phone_number(phone_number: str) -> bool:
    """
    检查手机号是否有效
    
    Args:
        phone_number: 手机号
    
    Returns:
        是否有效
    """
    phone = phone_number.strip()
    
    # 移除+号进行长度检查
    clean_phone = phone.lstrip('+')
    
    # 基本长度检查
    if len(clean_phone) < 7 or len(clean_phone) > 15:
        return False
    
    # 检查是否全为数字
    if not clean_phone.isdigit():
        return False
    
    return True


def extract_phone_info(phone_number: str) -> Tuple[str, Optional[str], str]:
    """
    提取手机号信息
    
    Args:
        phone_number: 手机号
    
    Returns:
        (完整号码, 国家区号, 本地号码)
    """
    phone = phone_number.strip()
    
    if phone.startswith('+'):
        # 尝试分离国家区号和本地号码
        # 这是一个简化的实现，实际可能需要更复杂的逻辑
        for i in range(1, 5):  # 国家区号通常是1-4位
            if len(phone) > i:
                country_code = phone[:i+1]  # 包含+号
                local_number = phone[i+1:]
                if len(local_number) >= 7:  # 本地号码至少7位
                    return phone, country_code, local_number
        return phone, None, phone[1:]
    else:
        # 没有+号，尝试检测
        detected_code = detect_country_code(phone)
        if detected_code:
            full_number = detected_code + phone
            return full_number, detected_code, phone
        else:
            return '+' + phone, None, phone


# 测试函数
def test_phone_utils():
    """测试手机号处理功能"""
    test_cases = [
        "8469858989",  # 你提到的越南号码
        "9608127650",  # 菲律宾号码
        "13800138000", # 中国号码
        "+639608127650", # 已有+号的菲律宾号码
        "1234567890",  # 无效号码
    ]
    
    print("🧪 测试手机号处理功能")
    print("=" * 50)
    
    for phone in test_cases:
        print(f"\n📱 原始号码: {phone}")
        
        # 检测国家区号
        country_code = detect_country_code(phone)
        print(f"   检测到的区号: {country_code}")
        
        # 格式化
        formatted = format_phone_number(phone)
        print(f"   格式化后: {formatted}")
        
        # 验证
        is_valid = is_valid_phone_number(phone)
        print(f"   是否有效: {is_valid}")
        
        # 提取信息
        full, country, local = extract_phone_info(phone)
        print(f"   完整号码: {full}")
        print(f"   国家区号: {country}")
        print(f"   本地号码: {local}")


if __name__ == "__main__":
    test_phone_utils()
