# Standalone installation inputs

`standalone.yaml` is an optional manually applied example, not the active service
chart or self-host installer. Before using it, create an installation-owned
`shizuha-mail-installation` ConfigMap in the same namespace with these keys:

- `POSTGRES_HOST`: the installation's PostgreSQL service DNS name.
- `SHIZUHA_ID_API_URL`: the installation's canonical internal ID API URL.
- `CELERY_BROKER_URL`: the installation's Redis broker endpoint.
- `CELERY_RESULT_BACKEND`: the installation's Redis result endpoint.

Do not copy another cluster's numeric ServiceIPs. Keep credentials in the
existing Secret references; this ConfigMap supplies endpoints only. If an
endpoint URL includes credentials, use a Secret-backed environment reference
instead. The active chart and portable installer own their own configuration.
