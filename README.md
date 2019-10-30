# setup
Python script for configuring new Ubuntu builds


One-line execution
------------------
```bash
wget -O - https://raw.githubusercontent.com/agoose77/setup/master/setup.py | python3
```

Download then execute
---------------------
```bash
wget https://raw.githubusercontent.com/agoose77/setup/master/setup.py
python3 setup.py
```

Patch & data files
------------------
```python
ROOT_CPACK_PATCH_URL = (
    "https://gist.github.com/agoose77/80e00a9baf1fb1a23e12c71f45431be9/raw"
)
GEANT4_CPACK_PATCH_URL = (
    "https://gist.github.com/agoose77/fba2fc5504933b7fb2c5b8c3cfd93529/raw"
)
TMUX_CONF_URL = (
    "https://gist.githubusercontent.com/agoose77/3e3b273cbfdb8a870c97ebb346beef8e/raw"
)
```
