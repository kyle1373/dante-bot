#!/bin/bash

# Define the virtual environment directory name
VENV_DIR="venv"

# Check if the virtual environment directory exists
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv $VENV_DIR
fi

# Activate the virtual environment
source $VENV_DIR/bin/activate
echo "Activated virtual environment..."

# Install requirements
echo "Installing requirements from requirements.txt..."
pip3 install -r requirements.txt

echo "Environment setup is complete."
