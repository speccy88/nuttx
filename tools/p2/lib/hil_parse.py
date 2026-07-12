def classify_log(text, success_marker):
    low=text.lower()
    if 'panic' in low or 'assert' in low: return 'panic'
    if 'unexpected reboot' in low: return 'reboot'
    if success_marker and success_marker in text: return 'success'
    return 'timeout'
