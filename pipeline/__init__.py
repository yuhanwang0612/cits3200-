"""Independent, versioned CITS3200 data pipeline.

Import concrete functions from :mod:`pipeline.runner`.  Keeping package import
side-effect free also avoids loading ``pipeline.runner`` twice when it is run
with ``python -m``.
"""
