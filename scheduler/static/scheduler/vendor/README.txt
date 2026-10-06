This folder is populated by running `python download_assets.py`
(from the project root) once, with internet access.

It downloads Bootstrap and Bootstrap Icons here so the app serves them
from your own machine instead of a CDN — faster, and works with no
internet at all (useful for a panel demo).

Until you run that script, this folder is empty and the page will be
unstyled (missing bootstrap.min.css / bootstrap-icons.css).
