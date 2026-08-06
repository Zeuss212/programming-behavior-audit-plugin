ARG BLUEDOT_BASE_IMAGE
FROM ${BLUEDOT_BASE_IMAGE}

ARG MYEXTENSION_WHEEL=myextension-0.2.1-py3-none-any.whl

COPY dist/${MYEXTENSION_WHEEL} /tmp/${MYEXTENSION_WHEEL}

RUN python -m pip install --no-cache-dir "/tmp/${MYEXTENSION_WHEEL}" \
    && python -m jupyter server extension enable myextension --sys-prefix \
    && python -m jupyter labextension enable myextension --level=sys_prefix \
    && rm "/tmp/${MYEXTENSION_WHEEL}"

ENV JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR=/workspace/result/behavior-audit

# ARK_API_KEY must be injected by the platform secret manager at runtime.
# Do not add credentials with ARG, ENV, COPY, or a startup command.
