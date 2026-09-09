"""Present so pytest puts ``proxy-app/`` on sys.path.

That is what lets ``tests/`` import both the ``proxy_app`` package and the
top-level ``seed`` module without an installed distribution or a PYTHONPATH
incantation in the README.
"""
