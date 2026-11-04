# Toolchain image for the OPP ns-3 simulator.
#
# The simulator is based on ns-3.17, whose bundled waf build system only runs
# under Python 2 and whose C++ was written for older compilers (the upstream
# README recommends gcc-5). Neither is available on current Ubuntu releases
# (24.04, 26.04), so this image recreates the environment the code was written
# for. The repository itself is bind-mounted at runtime, not copied in.
#
#   Ubuntu 18.04
#   gcc-5 / g++-5  -> compiles ns-3 (exported as CC / CXX)
#   python2 (2.7)  -> runs ./waf
#   python3 (3.8)  -> runs the experiment_reproduction runners
FROM docker.io/library/ubuntu:18.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# 18.04 is past standard support. If its packages have been moved off the main
# mirror, fall back to old-releases.ubuntu.com automatically.
RUN set -eux; \
    if ! apt-get update; then \
        sed -i -e 's|http://archive.ubuntu.com|http://old-releases.ubuntu.com|g' \
               -e 's|http://security.ubuntu.com|http://old-releases.ubuntu.com|g' \
               /etc/apt/sources.list; \
        apt-get update; \
    fi; \
    apt-get install -y --no-install-recommends \
        build-essential gcc-5 g++-5 \
        python python3.8 \
        ca-certificates git; \
    ln -sf /usr/bin/python3.8 /usr/local/bin/python3; \
    rm -rf /var/lib/apt/lists/*; \
    python2 --version; python3 --version; g++-5 --version | head -n1

ENV CC=gcc-5 \
    CXX=g++-5 \
    OPP_PYTHON2=python2 \
    PYTHONDONTWRITEBYTECODE=1
