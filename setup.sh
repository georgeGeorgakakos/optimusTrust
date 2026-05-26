#!/usr/bin/env bash
# Bootstrap: fetch the optimusPy client and install dependencies.
set -e

echo "==> Cloning optimusPy to obtain optimusdb_client.py ..."
rm -rf .optimuspy
git clone --depth 1 https://github.com/georgeGeorgakakos/optimusPy.git .optimuspy

echo "==> Copying client into project root ..."
cp .optimuspy/optimusdb_client.py .

echo "==> Installing dependencies ..."
pip3 install -r requirements.txt

echo "==> Done. Try: python3 tms_demo.py health"
