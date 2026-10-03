"""Legacy automation retired. Use MakeCloud's reviewed plan workflow.

Configure provider identity in environment variables, workload identity, or
cloud CLI credentials on the trusted backend. Never commit tokens or keys.
"""
if __name__ == '__main__':
    raise SystemExit('Use uvicorn app:app. Legacy unreviewed credential automation has been retired.')
