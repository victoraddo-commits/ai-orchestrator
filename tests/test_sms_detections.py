"""core.sms detections — OTP variants, security alerts, suspicious, promo."""

from core.sms.detections import classify, detect_otp, has_otp
from core.sms.schema import SmsClassification


def _codes(body):
    return detect_otp(body)


def test_detect_otp_keyword_before_code():
    assert "482913" in _codes("Your verification code is 482913")


def test_detect_otp_digits_before_keyword():
    assert "483920" in _codes("483920 is your OTP. Do not share it with anyone.")


def test_detect_otp_colon_form():
    assert "556677" in _codes("OTP: 556677")


def test_detect_otp_one_time_passcode():
    assert "123456" in _codes("Your one-time passcode: 123456")


def test_detect_otp_prefixed_google_style():
    assert "754321" in _codes("G-754321 is your Google verification code")


def test_detect_otp_lowercase_uppercase():
    assert "9012" in _codes("use code 9012 to continue")
    assert "9012" in _codes("USE CODE 9012 TO CONTINUE")


def test_detect_otp_none_for_plain_message():
    assert _codes("Your order has shipped. Track it in the app.") == []
    assert has_otp("Your order has shipped.") is False


def test_detect_otp_ignores_long_numbers():
    # 12-digit reference is not an OTP
    assert _codes("Reference 123456789012 for your transfer") == []


def test_classify_otp():
    assert classify("Your verification code is 482913") == SmsClassification.OTP


def test_classify_security_alert():
    assert classify("Security alert: a new sign-in to your account") == SmsClassification.SECURITY_ALERT


def test_classify_suspicious():
    assert classify("You have won a prize! Claim your reward now") == SmsClassification.SUSPICIOUS


def test_classify_promotional():
    assert classify("50% off sale today only. Shop now!") == SmsClassification.PROMOTIONAL


def test_classify_other():
    assert classify("Your package was delivered.") == SmsClassification.OTHER
