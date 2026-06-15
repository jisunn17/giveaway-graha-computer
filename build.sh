#!/bin/bash
# Build script for Render
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium
