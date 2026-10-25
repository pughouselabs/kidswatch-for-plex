import logging

# httpx/httpcore log every request URL at INFO; some URLs carry keys (Tautulli, the HA webhook).
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)
