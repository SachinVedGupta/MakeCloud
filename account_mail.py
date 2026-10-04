"""Transactional account mail over HTTPS or authenticated TLS SMTP."""
import os
import smtplib
import ssl
import httpx
from email.message import EmailMessage
from fastapi import HTTPException
from cloud_connections import base_url


def ready():
    return bool(os.getenv('MAKECLOUD_MAIL_FROM') and os.getenv('MAKECLOUD_PUBLIC_URL') and (os.getenv('MAKECLOUD_RESEND_API_KEY') or all(os.getenv(key) for key in ('MAKECLOUD_SMTP_HOST', 'MAKECLOUD_SMTP_USER', 'MAKECLOUD_SMTP_PASSWORD'))))


def send(address, token, kind):
    if not ready():
        raise HTTPException(503, 'Account email is not configured. Contact the MakeCloud operator.')
    label = 'Verify your email' if kind == 'verify' else 'Reset your password'
    message = EmailMessage()
    message['From'] = os.environ['MAKECLOUD_MAIL_FROM']
    message['To'] = address
    message['Subject'] = label + ' for MakeCloud'
    message.set_content(f'{label}:\n{base_url()}/#{kind}={token}\n\nThis link expires in 30 minutes and can be used once. If you did not request it, ignore this email.')
    if os.getenv('MAKECLOUD_RESEND_API_KEY'):
        try:
            response = httpx.post('https://api.resend.com/emails', headers={'Authorization': 'Bearer ' + os.environ['MAKECLOUD_RESEND_API_KEY']}, json={'from': message['From'], 'to': [address], 'subject': message['Subject'], 'text': message.get_content()}, timeout=20, follow_redirects=False)
            response.raise_for_status()
            return
        except httpx.HTTPError as exc:
            raise HTTPException(503, 'Account email could not be delivered. Request a new link shortly.') from exc
    try:
        with smtplib.SMTP(os.environ['MAKECLOUD_SMTP_HOST'], int(os.getenv('MAKECLOUD_SMTP_PORT', '587')), timeout=20) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(os.environ['MAKECLOUD_SMTP_USER'], os.environ['MAKECLOUD_SMTP_PASSWORD'])
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(503, 'Account email could not be delivered. Request a new link shortly.') from exc
