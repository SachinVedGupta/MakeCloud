import httpx
import pytest
from fastapi import HTTPException
import account_mail


@pytest.mark.parametrize('fails', [False, True])
def test_https_account_mail_does_not_expose_delivery_errors(monkeypatch, fails):
    monkeypatch.setenv('MAKECLOUD_RESEND_API_KEY', 'fixture-key')
    monkeypatch.setenv('MAKECLOUD_MAIL_FROM', 'MakeCloud <beta@example.com>')
    monkeypatch.setenv('MAKECLOUD_PUBLIC_URL', 'https://makecloud.example.com')
    def deliver(url, **kwargs):
        assert url == 'https://api.resend.com/emails'
        assert kwargs['headers']['Authorization'] == 'Bearer fixture-key'
        assert kwargs['follow_redirects'] is False
        assert kwargs['json']['to'] == ['customer@example.com']
        assert '/#reset=one-time-token' in kwargs['json']['text']
        return httpx.Response(503 if fails else 200, text='private error', request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx, 'post', deliver)
    if fails:
        with pytest.raises(HTTPException) as exc:
            account_mail.send('customer@example.com', 'one-time-token', 'reset')
        assert exc.value.status_code == 503 and 'private' not in exc.value.detail
    else:
        account_mail.send('customer@example.com', 'one-time-token', 'reset')
