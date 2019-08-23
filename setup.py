import json
import logging
import os
import re
import sys
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from subprocess import check_output
from typing import NamedTuple, List, Dict

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOGLEVEL", "INFO"))

ch = logging.StreamHandler()
ch.setLevel(logging.INFO)

formatter = logging.Formatter("{prefix}{message}", style="{")
ch.setFormatter(formatter)

logger.addHandler(ch)

HOME_PATH = Path.home()
ZSHRC_PATH = HOME_PATH / ".zshrc"
ZPROFILE_PATH = HOME_PATH / ".zprofile"
GPG_HOME_PATH = HOME_PATH / ".gnupg"
ROOT_CPACK_PATCH_URL = (
    "https://gist.github.com/agoose77/80e00a9baf1fb1a23e12c71f45431be9/raw"
)
GEANT4_CPACK_PATCH_URL = (
    "https://gist.github.com/agoose77/fba2fc5504933b7fb2c5b8c3cfd93529/raw"
)

valid = re.compile(r"[^\s=\(\)%]+=")


def reload_plumbum_env():
    """Reloads `local.env` after re-sourcing .zshrc"""
    output = cmd.zsh("-c", "source ~/.zshrc && env")
    env = {
        k: v
        for k, v in (l.split("=", 1) for l in output.splitlines() if valid.match(l))
    }
    local.env.update(**env)
    return env


class GitTag(NamedTuple):
    name: str
    tarball_url: str


class SysconfigData(NamedTuple):
    paths: List[str]
    config_vars: Dict[str, str]
    executable: str


_depth = 0


# Logging and utilities ################################################################################################
@contextmanager
def context():
    global _depth
    _depth += 1
    yield
    _depth -= 1


def prefix():
    return "   " * _depth


def log(message, level=logging.INFO):
    try:
        colors = plumbum.colors
    except NameError:
        pass
    else:
        log_level_to_colour = {
            logging.DEBUG: colors.fg,
            logging.INFO: colors.info,
            logging.WARN: colors.warn,
            logging.ERROR: colors.fatal,
            logging.CRITICAL: colors.fatal & colors.bold,
        }
        message = log_level_to_colour[level] | message
    logger.log(level, message, extra={"prefix": prefix()})


def installer(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        arg_strings = [repr(a) for a in args]
        kwarg_strings = [f"{k}={v!r}" for k, v in kwargs.items()]
        func_string = f"{func.__name__}({', '.join([*arg_strings, *kwarg_strings])})"

        log(f"Running {func_string}")
        with context():
            try:
                result = func(*args, **kwargs)
            except Exception:
                log(f"Execution of {func_string} failed", level=logging.ERROR)
                raise

        log(f"Finished {func_string}")
        return result

    return wrapper


@contextmanager
def detect_changed_files(directory):
    path = Path(directory).expanduser()
    before_files = set(path.iterdir())
    changed_files = set()
    yield changed_files
    changed_files |= set(path.iterdir()) - before_files


#  Installers ##########################################################################################################
def install_pip():
    return check_output(["sudo", "apt", "install", "-y", "python3-pip"], shell=False)


def install_plumbum():
    output = check_output([sys.executable, "-m", "pip", "install", "plumbum"])

    import site

    sys.path.append(site.getusersitepackages())
    return output


def install_with_apt(*packages):
    return (cmd.sudo[cmd.apt[("install", "-y", *packages)]] << "\n")()


def install_with_pip(*packages):
    return check_output([sys.executable, "-m", "pip", "install", *packages])


def install_with_snap(*packages, classic=False):
    if classic:
        packages.append("--classic")

    (cmd.sudo[cmd.snap[("install", *packages)]] << "\n")()


def install_powerline_fonts():
    with local.cwd("/tmp"):
        cmd.git("clone", "https://github.com/powerline/fonts.git")
        with local.cwd(local.cwd / "fonts"):
            local[local.cwd / "install.sh"]()


def update_path(*components):
    contents = ZSHRC_PATH.read_text()

    def replacer(match_obj):
        path_str = match_obj.group(1)
        path = path_str.split(":")
        for component in components:
            if not component in path:
                path.insert(0, component)
        return f'export PATH="{":".join(path)}"'

    ZSHRC_PATH.write_text(re.sub('export PATH="(.*)"', replacer, contents))
    reload_plumbum_env()


def append_init_scripts(*scripts: str):
    zshrc_contents = ZSHRC_PATH.read_text()
    if not zshrc_contents.endswith("\n"):
        zshrc_contents += "\n"
    zshrc_contents += "\n".join(scripts)
    ZSHRC_PATH.write_text(zshrc_contents)
    reload_plumbum_env()


def prepend_init_scripts(*scripts: str):
    zshrc_contents = "\n".join(scripts)
    if not zshrc_contents.endswith("\n"):
        zshrc_contents += "\n"
    ZSHRC_PATH.write_text(zshrc_contents + ZSHRC_PATH.read_text())
    reload_plumbum_env()


def install_zsh(theme="agnoster"):
    install_with_apt("zsh")
    cmd.sudo[cmd.chsh["-s", cmd.which("zsh").strip()]]()
    os.system(
        'sh -c "$(wget https://raw.githubusercontent.com/robbyrussell/oh-my-zsh/master/tools/install.sh -O -)"'
    )

    # Update ZSHRC theme
    zshrc_contents = re.sub(
        'ZSH_THEME=".*"', rf'ZSH_THEME="{theme}"', ZSHRC_PATH.read_text()
    )

    # Enable PATH variable
    zshrc_contents = re.sub(r"# (export PATH.*)", "$1", zshrc_contents)
    # Enable shrink-path & z plugins
    zshrc_contents = re.sub(
        r"(plugins=\([^\)]+)\)", "$1 shrink-path z\)", zshrc_contents
    )
    zshrc_contents = f"""
# Hide prompt
DEFAULT_USER=`whoami`

{zshrc_contents}

# Contract prompt directory
prompt_dir() {{
    prompt_segment blue $CURRENT_FG $(shrink_path -l -t)
}}
"""
    ZSHRC_PATH.write_text(zshrc_contents)

    # Fix sourcing profile in ZSH
    ZPROFILE_PATH.write_text(
        'for file in /etc/profile.d/*.sh; do source "${file}"; done'
    )

    # Add useful paths to PATH
    update_path("$HOME/.local/bin", "$HOME/bin", "/usr/local/bin")


def install_exa(github_token: str):
    query = """
{
  repository(owner: "ogham", name: "exa") {
    releases(first: 1, orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes {
        name
        releaseAssets(first: 10) {
          nodes{
            name
            contentType
            downloadUrl
          }
        }
      }
    }
  }
}
  """
    result = execute_github_graphql_query(github_token, query)
    asset_nodes = next(
        r["releaseAssets"]["nodes"]
        for r in result["data"]["repository"]["releases"]["nodes"]
    )
    url = next(
        n["downloadUrl"]
        for n in asset_nodes
        if "linux" in n["name"] and n["name"].endswith(".zip")
    )
    with local.cwd("/tmp"):
        # Download zip
        with detect_changed_files(local.cwd) as changed_files:
            cmd.wget(url)
        zip_path, = changed_files

        # Unpack zip
        with detect_changed_files(local.cwd) as changed_files:
            cmd.unzip(zip_path)

        bin_path, = changed_files
        dest_path = local.env.home / ".local" / "bin" / "exa"
        cmd.mv(bin_path, dest_path)

    # Setup aliases
    append_init_scripts(
        """
# Exa aliases
alias xa='exa'
alias ls='exa'
alias lt='exa --tree'
alias lg='exa --git -hl'
alias l='exa -al'
alias ll='exa -l'
alias lll='exa -l --colour=always | less'
"""
    )


def install_fd():
    install_with_apt("fd-find")
    append_init_scripts(
        """
# Fd-find alias
alias fd='fdfind'
"""
    )


def install_tmux():
    install_with_apt("tmux")
    tmux_conf = """
# Use F1 as modifier
unbind C-b
set -g prefix F1
bind F1 send-prefix

# split panes using h and b
bind h split-window -h
bind v split-window -v
unbind '"'
unbind %

# switch panes using Alt-arrow without prefix
bind -n M-Left select-pane -L
bind -n M-Right select-pane -R
bind -n M-Up select-pane -U
bind -n M-Down select-pane -D

# Enable mouse mode (tmux 2.1 and above)
set -g mouse on

# reload config file (change file location to your the tmux.conf you want to use)
bind r source-file ~/.tmux.conf
"""
    (HOME_PATH / ".tmux.conf").write_text(tmux_conf)


def install_chrome():
    deb_name = "google-chrome-stable_current_amd64.deb"
    with local.cwd("/tmp"):
        cmd.wget(f"https://dl.google.com/linux/direct/{deb_name}")
        cmd.sudo[cmd.dpkg["-i", deb_name]]()


def install_numix_theme():
    cmd.sudo[cmd.add_apt_repository["ppa:numix/ppa"]]()
    cmd.sudo[cmd.apt["update"]]()
    install_with_apt("numix-icon-theme-circle")


def install_canta_theme():
    install_numix_theme()

    with local.cwd("/tmp"):
        cmd.git("clone", "https://github.com/vinceliuice/Canta-theme.git")
        with local.cwd("Canta-theme"):
            local[local.cwd / "install.sh"]("-i")

    cmd.gsettings("set", "org.gnome.desktop.interface", "icon-theme", "Canta")
    cmd.gsettings(
        "set", "org.gnome.desktop.interface", "gtk-theme", "Canta-dark-compact"
    )
    cmd.dconf(
        "write", "/org/gnome/shell/extensions/user-theme/name", "'Canta-dark-compact'"
    )


def install_gnome_tweak_tool():
    (cmd.sudo[cmd.apt["install", "gnome-tweak-tool"]] << "\n")()


def install_gnome_theme():
    install_with_apt("chrome-gnome-shell")
    cmd.google_chrome(
        "https://chrome.google.com/webstore/detail/gnome-shell-integration/gphhapmejobijbbhgpjhcjognlahblep?utm_source=inline-install-disabled"
    )
    cmd.google_chrome("https://extensions.gnome.org/extension/19/user-themes/")


def install_pandoc(github_token: str):
    query = """
{
  repository(owner: "jgm", name: "pandoc") {
    releases(first: 1, orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes {
        name
        releaseAssets(first: 10) {
          nodes{
            name
            contentType
            downloadUrl
          }
        }
      }
    }
  }
}
  """
    result = execute_github_graphql_query(github_token, query)
    release, = result["data"]["repository"]["releases"]["nodes"]

    deb_url = next(
        n["downloadUrl"]
        for n in release["releaseAssets"]["nodes"]
        if n["name"].endswith(".deb")
    )
    log(f"Found {release['name']}, downloading deb from {deb_url}")

    with local.cwd("/tmp"):
        with detect_changed_files(local.cwd) as changed_files:
            cmd.aria2c(deb_url, "-j", "10", "-x", "10")
        deb_path, = changed_files
        install_with_apt(deb_path)


def install_tex():
    with local.cwd("/tmp"):
        cmd.wget("mirror.ctan.org/systems/texlive/tlnet/install-tl-unx.tar.gz")
        cmd.tar("-xvf", "install-tl-unx.tar.gz")

        directory = next((p for p in (local.cwd // "install-tl*") if p.is_dir()))

        path_component = None
        pattern = re.compile(r"Most importantly, add (.*)")

        with local.cwd(directory):
            proc = (cmd.sudo[local[local.cwd / "install-tl"]] << "I\n").popen()
            for out, err in proc:
                if err:
                    log(err, logging.ERROR)
                if out:
                    log(out, logging.INFO)
                    match = pattern.match(out)
                    if match:
                        path_component = match.group(1)

    if path_component is not None:
        update_path(path_component)


def get_system_python_version() -> str:
    from sys import version_info

    return f"{version_info.major}.{version_info.minor}.{version_info.micro}"


def install_pyenv_sys_python(system_venv_name: str):
    """
    Install the system Python into pyenv's versions directory using venv
    """
    install_with_apt("python3-venv")

    # Create venv
    pyenv_root = local.env.home / ".pyenv"
    pyenv_versions_dir = pyenv_root / "versions"
    venv_path = pyenv_versions_dir / system_venv_name
    local[sys.executable]("-m", "venv", venv_path, "--system-site-packages")

    # Set as system
    cmd.pyenv("global", system_venv_name)

    # Produce shims for pip, python (required when they don't exist and we dont call into pyenv init)
    cmd.pyenv("rehash")

    with local.env(PYENV_VERSION=system_venv_name):
        # Install some utilities
        cmd.pip(
            "install", "nbdime", "jupyter", "jupyterlab", "jupyter-console", "makey"
        )

        # Setup nbdime as git diff engine
        cmd.nbdime("config-git", "--enable", "--global")

    append_init_scripts('alias jc="jupyter console"', 'alias jl="jupyter lab"')


def install_pyenv(system_venv_name: str):
    """
    Install PyEnv for managing Python versions & virtualenvs
    :return:
    """
    # Install pyenv
    (
        cmd.wget[
            "-O",
            "-",
            "https://github.com/pyenv/pyenv-installer/raw/master/bin/pyenv-installer",
        ]
        | cmd.bash
    )()
    update_path("$HOME/.pyenv/bin")

    # Add init scripts
    append_init_scripts('eval "$(pyenv init -)"', 'eval "$(pyenv virtualenv-init -)"')

    install_pyenv_sys_python(system_venv_name)


def install_development_virtualenv(python_version: str, virtualenv_name: str = None):
    """
    Install Jupyter within a new virtual environment

    :param python_version: Python interpreter version string
    :param virtualenv_name: Name of virtual environment
    :return:
    """
    # Install npm
    install_with_apt("npm")

    if not python_version:
        python_version = get_system_python_version()

    # Install a particular interpreter (from source)
    if python_version != get_system_python_version():
        log("Installing Python version")
        cmd.pyenv["install", python_version].with_env(
            PYTHON_CONFIGURE_OPTS="--enable-shared"
        )()

    # Create virtualenv
    log("Creating virtualenv")
    cmd.pyenv("virtualenv", python_version, virtualenv_name)

    # Install packages
    log("Installing jupyter packages with pip")
    with local.env(PYENV_VERSION=virtualenv_name):
        cmd.pip(
            "install",
            "jupyter",
            "jupyterlab",
            "matplotlib",
            "ipympl",
            "numpy-html",
            "jupytex",
            "bqplot",
            "numba",
        )

        # Conda for scientific libraries
        try:
            conda = get_conda(virtualenv_name)
        except FileNotFoundError:
            cmd.pip("install", "scipy", "numpy")
        else:
            conda("install", "scipy", "numpy")

        # Install labextensions
        log("Installing lab extensions")
        cmd.jupyter(
            "labextension",
            "install",
            "@jupyter-widgets/jupyterlab-manager",
            "jupyter-matplotlib",
            "bqplot",
            "@agoose77/jupyterlab-markup",
            "@telamonian/theme-darcula",
            "@jupyterlab/katex-extension",
        )


def install_micro():
    """
    Install the micro editor
    :return:
    """
    install_with_snap("micro", classic=True)

    # Set default editor in ZSH
    uncommented = re.sub(
        r"# Preferred editor(?:.|\n)*# fi",
        lambda m: m.lastgroup.replace("# ", ""),
        ZSHRC_PATH.read_text(),
    )
    ZSHRC_PATH.write_text(re.sub("(EDITOR=).*", r"\1'micro'", uncommented))


def install_keyboard_shortcuts():
    install_with_apt("xdotool")

    custom_bindings = [
        ("Screenshot area with Flameshot", "flameshot gui", "Print"),
        ("Spotify", "spotify", "<Super>s"),
        # Make custom bindings for audio to avoid overwriting defaults
        *(
            (name, f"xdotool key --clearmodifiers {key}", binding)
            for name, key, binding in
            # Create "xdotool" command for each key in the following
            [
                ("Next", "XF86AudioNext", "<Alt><Super>Right"),
                ("Previous", "XF86AudioPrev", "<Alt><Super>Left"),
                ("Play/pause", "XF86AudioPlay", "<Alt><Super>Space"),
                ("Volume up", "XF86AudioRaiseVolume", "<Alt><Super>Up"),
                ("Volume down", "XF86AudioLowerVolume", "<Alt><Super>Down"),
            ]
        ),
    ]

    media_settings_path = "org.gnome.settings-daemon.plugins.media-keys"
    custom_binding_paths = [
        f"/{media_settings_path.replace('.', '/')}/custom-keybindings/custom{i}/"
        for i in range(len(custom_bindings))
    ]

    # Set normal keybindings
    bindings = {
        "home": "<Super>f",
        "email": "<Super>e",
        "terminal": "<Super>t",
        "www": "<Super>w",
        "control-center": "<Super>x",
        "custom-keybindings": custom_binding_paths,
    }

    for name, binding in bindings.items():
        cmd.gsettings("set", media_settings_path, name, repr(binding))

    # Set custom keybindings
    for path, (name, command, binding) in zip(custom_binding_paths, custom_bindings):
        cmd.gsettings(
            "set", f"{media_settings_path}.custom-keybinding:{path}", "name", repr(name)
        )
        cmd.gsettings(
            "set",
            f"{media_settings_path}.custom-keybinding:{path}",
            "command",
            repr(command),
        )
        cmd.gsettings(
            "set",
            f"{media_settings_path}.custom-keybinding:{path}",
            "binding",
            repr(binding),
        )


def install_gnome_favourites():
    favourites = [
        "google-chrome.desktop",
        "org.gnome.Nautilus.desktop",
        "org.gnome.Terminal.desktop",
        "mailspring_mailspring.desktop",
        "pycharm-professional_pycharm-professional.desktop",
        "clion_clion.desktop",
        "webstorm_webstorm.desktop",
        "spotify_spotify.desktop",
        "atom_atom.desktop",
        "org.gnome.Evince.desktop",
    ]

    cmd.gsettings("set", "org.gnome.shell", "favorite-apps", str(favourites))


def create_gpg_key(name, email_address, key_length):
    import gnupg

    gpg = gnupg.GPG(homedir=str(GPG_HOME_PATH))
    input_data = gpg.gen_key_input(
        key_type="RSA", key_length=key_length, name_real=name, name_email=email_address
    )
    log("Generating GPG key")
    key = gpg.gen_key(input_data)
    log("Exporting GPG key")
    key_data = next(k for k in gpg.list_keys() if k["fingerprint"] == str(key))
    signing_key = key_data["keyid"]
    return gpg.export_keys(signing_key), signing_key


def install_git(name, email_address):
    install_with_apt("git")

    cmd.git("config", "--global", "user.email", email_address)
    cmd.git("config", "--global", "user.name", name)

    append_init_scripts(
        "# TODO tracking",
        "alias todo='git grep --no-pager  -EI \"TODO|FIXME\"'",
        "alias td='todo'",
        """update(){
    cd $1
    git pull
    cd -
}
alias upd='update'
""",
    )


def install_gnupg(name, email_address, key_length):
    install_with_apt("gnupg")
    install_with_pip("gnupg")
    # Create public key and copy to clipboard
    public_key, signing_key = create_gpg_key(name, email_address, key_length)
    (cmd.echo[public_key] | cmd.xclip["-sel", "clip"]) & plumbum.BG

    # Add key to github
    cmd.google_chrome("https://github.com/settings/gpg/new")
    cmd.git("config", "--global", "commit.gpgsign", "true")
    cmd.git("config", "--global", "user.signingkey", signing_key)

    agent_path = GPG_HOME_PATH / "gpg-agent.conf"
    agent_path.touch()
    agent_path.write_text(
        agent_path.read_text()
        + """
default-cache-ttl 28800
max-cache-ttl 28800"""
    )

    append_init_scripts("# GPG signing\nexport GPG_TTY=$(tty)")


def make_or_find_sources_dir():
    sources = Path("~/Sources").expanduser()
    if not sources.exists():
        sources.mkdir()
    return sources


def make_or_find_libraries_dir():
    libraries = Path("~/Libraries").expanduser()
    if not libraries.exists():
        libraries.mkdir()
    return libraries


class TokenInvalidError(ValueError):
    pass


def graphql_errors_to_string(errors):
    messages = []
    for error in errors:
        locations = [
            f'(line {p["line"]}, column {p["column"]})' for p in error["locations"]
        ]
        messages.append(f'{error["message"]} on {", ".join(locations)}')
    return "\n".join(messages)


def execute_github_graphql_query(token: str, query: str) -> dict:
    import urllib.request as request
    import urllib.error as error

    req = request.Request(
        "https://api.github.com/graphql",
        method="POST",
        data=json.dumps({"query": query}).encode(),
        headers={"Authorization": f"token {token}"},
    )

    try:
        resp = request.urlopen(req)
    except error.HTTPError as err:
        if err.code == 401:
            raise TokenInvalidError(f"Token {token!r} was invalid!") from err
        raise

    result = json.loads(resp.read())
    if "errors" in result:
        raise ValueError(graphql_errors_to_string(result["errors"]))
    return result


def validate_github_token(token: str) -> str:
    """
    Test GitHub token to ensure it is valid.

    :param token: GitHub personal access token
    :return: GitHub personal access token
    """
    test_query = """
    {
          repository(owner:"root-project", name: "root") {
            name
          }
    }
    """
    execute_github_graphql_query(token, test_query)
    return token


def find_latest_github_tag(token: str, owner: str, name: str) -> GitTag:
    """
    Find latest Tag object from GitHub repo using GraphQL

    :param token: GitHub personal authentication token
    :param owner: Repository owner
    :param name: Repository name
    :return:
    """
    from string import Template

    query_template = """
{
    repository(owner:"$owner", name: "$name") {
        refs(refPrefix: "refs/tags/", first: 1, orderBy: {field: ALPHABETICAL, direction: DESC}) {
          edges {
            node {
              name
              target {
                __typename
                ... on Tag {
                  name
                  target {
                    ... on Commit {
                      tarballUrl
                    }
                  }
                }
                ... on Commit {
                  tarballUrl
                }
              }
            }
          }
        }
    }
}
    """
    query = Template(query_template).substitute(owner=owner, name=name)
    result = execute_github_graphql_query(token, query)

    edge, = result["data"]["repository"]["refs"]["edges"]
    obj = edge["node"]
    tag = obj["name"]

    while "target" in obj:
        obj = obj["target"]
    url = obj["tarballUrl"]
    return GitTag(name=tag, tarball_url=url)


def get_pyenv_sysconfig_data(virtualenv_name: str) -> SysconfigData:
    """
    Return the results of `sysconfig.get_paths()` and `sysconfig.get_config_vars()` from the required virtualenv

    :param virtualenv_name: Name of virtual environment
    :return:
    """
    result = json.loads(
        cmd.python.with_env(PYENV_VERSION=virtualenv_name)(
            "-c",
            """
import sysconfig, json, sys
print(json.dumps({'paths':sysconfig.get_paths(), 
                  'config_vars':sysconfig.get_config_vars(),
                  'executable': sys.executable}))
            """,
        )
    )
    return SysconfigData(**result)


def get_conda(virtualenv_name=None):
    try:
        shim = cmd.conda
    except AttributeError:
        raise FileNotFoundError

    if virtualenv_name is not None:
        shim = shim.with_env(PYENV_VERSION=virtualenv_name)

    if not shim & plumbum.TF:
        raise FileNotFoundError
    return shim


def cmake_options_from_dict(opts):
    return [f"D{f}={v}" for f, v in opts.items()]


def install_root_from_source(virtualenv_name: str, n_threads: int, github_token: str):
    """
    Find latest ROOT sources, compile them, and link to the Python virtual environment
    :param virtualenv_name: name of PyEnv environment to link against
    :param n_threads: number of threads to use for compiling
    :param github_token: GitHub personal authentication token
    :return:
    """
    tag = find_latest_github_tag(github_token, "root-project", "root")
    log(f"Found latest root {tag.name}")

    # Install deps
    install_with_apt(
        "libx11-dev",
        "libxpm-dev",
        "libxft-dev",
        "libxext-dev",
        "libpng-dev",
        "libjpeg-dev",
    )

    # Find various paths for virtual environment
    sysconfig_data = get_pyenv_sysconfig_data(virtualenv_name)

    lib_dir_path = Path(sysconfig_data.config_vars["LIBDIR"])
    python_bin_path = Path(sysconfig_data.executable)
    python_lib_path = lib_dir_path / sysconfig_data.config_vars["LDLIBRARY"]
    python_include_path = Path(sysconfig_data.paths["include"])

    cmake_flags = {
        "PYTHON_INCLUDE_DIR": python_include_path,
        "PYTHON_LIBRARY": python_lib_path,
        "PYTHON_EXECUTABLE": python_bin_path,
        "PYTHON": "ON",
        "MINUIT2": "ON",
    }

    log(f"Installing root {tag}")
    with local.cwd(make_or_find_libraries_dir()):
        cmd.makey[
            (
                tag.tarball_url,
                "-j",
                n_threads,
                "-p",
                ROOT_CPACK_PATCH_URL,
                f"--version={tag.name.replace('v', '').replace('-', '.')}",
                "--verbose",
                "--copt",
                *cmake_options_from_dict(cmake_flags),
            )
        ] & plumbum.FG

    # Insert this at start of zshrc to avoid adding /usr/local/bin to head of path
    prepend_init_scripts(". /opt/root/bin/thisroot.sh")


def install_geant4(github_token: str, n_threads: int):
    tag = find_latest_github_tag(github_token, "Geant4", "geant4")
    cmake_flags = {
        "GEANT4_INSTALL_DATA": "ON",
        "GEANT4_USE_OPENGL_X11": "ON",
        "GEANT4_USE_GDML": "ON",
    }

    install_with_apt("libxerces-c-dev")

    with local.cwd(make_or_find_libraries_dir()):
        cmd.makey[
            (
                tag.tarball_url,
                "-j",
                n_threads,
                "-p",
                GEANT4_CPACK_PATCH_URL,
                "--copt",
                *cmake_options_from_dict(cmake_flags),
                "--dflag",
                # Exclude this path because it's a recursive symlink which causes issues
                "path-exclude=/usr/local/lib/Geant4-*/Linux-g++/*",
                "--verbose",
            )
        ] & plumbum.FG
    prepend_init_scripts(
        """
cd $(dirname $(which geant4.sh))
. geant4.sh
cd - > /dev/null"""
    )


def bootstrap():
    """Install system pip, and subsequently plumbum"""
    install_pip()
    install_plumbum()

    # Import modules
    global plumbum, cmd, local
    import plumbum
    from plumbum import cmd, local
    import plumbum.colors


NO_DEFAULT = object()


def get_user_input(prompt: str, default=NO_DEFAULT, converter=None):
    """Get the name of the main virtual environment"""
    while True:
        if default is NO_DEFAULT:
            value = input(f"{prompt}: ")
            if not value:
                log(f"A value is required! Try again.", level=logging.ERROR)
                continue
        else:
            value = input(f"{prompt} [{default}]: ")
            if not value:
                value = default

        if converter is not None:
            try:
                value = converter(value)
            except ValueError:
                log(f"Invalid value {value!r}! Try again.", level=logging.ERROR)
                continue

        return value


def get_max_system_threads() -> int:
    """Return the number of threads available on the system."""
    return int(check_output(["grep", "-c", "cores", "/proc/cpuinfo"]).decode().strip())


def convert_number_threads(n_total_threads: int, n_threads_str: str) -> int:
    """Validate and clamp requested number of threads string to those available.

    :param n_total_threads: number of total threads
    :param n_threads_str: string of requested number of threads
    :return:
    """
    n_threads = int(n_threads_str)
    if not 0 < n_threads <= n_total_threads:
        raise ValueError(f"Invalid number of threads {n_threads}!")
    return n_threads


def yes_no_to_bool(answer: str) -> bool:
    """Convert prompt-like yes/no response to a bool.

    :param answer: yes/no response
    :return:
    """
    return answer.lower().strip() in {"y", "yes", "1"}


# Decorate all installer functions
for name, value in {**globals()}.items():
    if name.startswith("install_") and callable(value):
        globals()[name] = installer(value)


class Config:
    """Configuration holder class.

    Defers evaluation of 'Deferred' configuration getters until they are looked up.
    """

    def __getattribute__(self, item):
        value = object.__getattribute__(self, item)
        if isinstance(value, DeferredValueFactory):
            value = value()
            setattr(self, item, value)
        return value

    def set(self, func):
        assert callable(func)
        setattr(self, func.__name__, deferred(func))


class DeferredValueFactory:
    """Wrapper class which represents a deferred configuration value"""

    def __init__(self, func):
        self.func = func

    def __call__(self):
        return self.func()


deferred = DeferredValueFactory


def deferred_user_input(prompt: str, default=NO_DEFAULT, converter=None):
    @deferred
    def user_input():
        return get_user_input(prompt, default, converter)

    return user_input


if __name__ == "__main__":
    # Lazy configuration
    config = Config()
    config.N_MAX_SYSTEM_THREADS = get_max_system_threads()
    config.N_BUILD_THREADS = deferred_user_input(
        "Enter number of build threads",
        config.N_MAX_SYSTEM_THREADS,
        lambda s: convert_number_threads(config.N_MAX_SYSTEM_THREADS, s),
    )
    config.DEVELOPMENT_VIRTUALENV_NAME = deferred_user_input(
        "Enter virtualenv name", "sci"
    )
    config.DEVELOPMENT_PYTHON_VERSION = deferred_user_input(
        "Enter Python version string", "miniconda3-latest", lambda s: s.strip().lower()
    )
    config.GIT_USER_NAME = deferred_user_input("Enter git user-name", "Angus Hollands")
    config.GIT_EMAIL_ADDRESS = deferred_user_input(
        "Enter git email-address", "goosey15@gmail.com"
    )
    config.GIT_KEY_LENGTH = deferred_user_input("Enter git key length", 4096, int)
    config.GITHUB_TOKEN = deferred_user_input(
        "Enter GitHub personal token", converter=validate_github_token
    )

    bootstrap()

    install_with_apt(
        "cmake",
        "cmake-gui",
        "build-essential",
        "aria2",
        "openssh-server",
        "checkinstall",
        "htop",
        "lm-sensors",
        "flameshot",
        "libreadline-dev",
        "libffi-dev",
        "libsqlite3-dev",
        "xclip",
        "libbz2-dev",
    )
    install_chrome()
    install_git(config.GIT_USER_NAME, config.GIT_EMAIL_ADDRESS)
    install_gnupg(config.GIT_USER_NAME, config.GIT_EMAIL_ADDRESS, config.GIT_KEY_LENGTH)
    install_zsh()
    install_exa(config.GITHUB_TOKEN)
    install_fd()
    install_tmux()

    config.SYSTEM_VENV_NAME = f"{get_system_python_version()}-system"
    install_pyenv(config.SYSTEM_VENV_NAME)
    install_development_virtualenv(
        config.DEVELOPMENT_PYTHON_VERSION, config.DEVELOPMENT_VIRTUALENV_NAME
    )
    install_with_snap("pycharm-professional", "clion", "webstorm", classic=True)
    install_gnome_theme()
    install_gnome_tweak_tool()
    install_canta_theme()
    install_with_snap("mailspring")
    install_with_snap("spotify")
    install_micro()
    install_keyboard_shortcuts()
    install_with_snap("atom", classic=True)
    install_gnome_favourites()
    install_with_apt("polari")
    install_with_apt("fzf")
    install_with_apt("ripgrep")
    install_powerline_fonts()
    install_pandoc(config.GITHUB_TOKEN)
    install_tex()

    # Install ROOT
    @config.set
    def CONDA_CMD():
        # If conda is installed at all
        try:
            return get_conda(config.DEVELOPMENT_VIRTUALENV_NAME)
        except FileNotFoundError:
            return None

    @config.set
    def USE_CONDA_ROOT():
        return config.CONDA_CMD and get_user_input(
            "Use Conda package for ROOT?", "y", yes_no_to_bool
        )

    if config.USE_CONDA_ROOT:
        config.CONDA_CMD("install", "-c", "conda-forge", "root")
    else:
        install_root_from_source(
            config.DEVELOPMENT_VIRTUALENV_NAME,
            config.N_BUILD_THREADS,
            config.GITHUB_TOKEN,
        )

    install_geant4(config.GITHUB_TOKEN, config.N_BUILD_THREADS)
