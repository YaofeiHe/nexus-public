from __future__ import annotations


HIGH_RISK_TERMS = {
    "install": ["安装", "pip install", "npm install", "brew install"],
    "login": ["登录", "login", "auth"],
    "read_secret": ["token", "cookie", "ssh key", ".env", "credentials", "浏览器 profile"],
    "submit_form": ["提交表单", "submit", "投递", "上传简历"],
    "send_message": ["发送邮件", "send email", "发消息", "私信"],
    "push": ["git push", "pull request", "release", "发布"],
    "bypass": ["验证码", "captcha", "绕过", "403", "风控"],
}


def detect_high_risk_actions(text: str) -> list[str]:
    lowered = text.lower()
    risks: list[str] = []
    for action, terms in HIGH_RISK_TERMS.items():
        if any(term.lower() in lowered for term in terms):
            risks.append(action)
    return risks


def safety_boundary() -> dict[str, object]:
    return {
        "mode": "read_only_discovery",
        "forbidden": [
            "install",
            "login",
            "read_token_cookie_env_ssh_browser_profile",
            "submit_form",
            "send_message_or_email",
            "push_pr_release",
            "bypass_captcha_403_rate_limit",
            "write_target_project_without_approval",
        ],
        "locale": "zh-CN",
        "market_context": "chinese_internet",
    }
