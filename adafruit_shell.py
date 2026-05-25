# SPDX-FileCopyrightText: 2017 Scott Shawcroft, written for Adafruit Industries
# SPDX-FileCopyrightText: Copyright (c) 2020 Melissa LeBlanc-Williams for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""
`adafruit_shell`
================================================================================

Python helper for running Shell scripts in Python


* Author(s): Melissa LeBlanc-Williams

Implementation Notes
--------------------

**Software and Dependencies:**

* Linux

"""

# imports
import fcntl
import fileinput
import os
import platform
import pwd
import re
import shutil
import stat
import subprocess
import sys
from datetime import datetime

import adafruit_platformdetect
from clint.textui import colored, prompt

__version__ = "0.0.0+auto.0"
__repo__ = "https://github.com/adafruit/Adafruit_Python_Shell.git"

# This must be by order of release
RASPI_VERSIONS = (
    "wheezy",
    "jessie",
    "stretch",
    "buster",
    "bullseye",
    "bookworm",
    "trixie",
)

WINDOW_MANAGERS = {
    "x11": "W1",
    "wayfire": "W2",
    "labwc": "W3",
}

FILE_MODES = {
    "+x": stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
    "+r": stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH,
    "+w": stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH,
    "a+x": stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
    "a+r": stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH,
    "a+w": stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH,
    "u+x": stat.S_IXUSR,
    "u+r": stat.S_IRUSR,
    "u+w": stat.S_IWUSR,
    "g+x": stat.S_IXGRP,
    "g+r": stat.S_IRGRP,
    "g+w": stat.S_IWGRP,
    "o+x": stat.S_IXOTH,
    "o+r": stat.S_IROTH,
    "o+w": stat.S_IWOTH,
}


# pylint: disable=too-many-public-methods
class Shell:
    """
    Class to help with converting Shell scripts over to Python. Having all
    the functions in one place makes updates easier and code shorter.
    """

    def __init__(self):
        self._group = None
        self._dirstack = []

    @staticmethod
    def select_n(message, selections):
        """
        Display a list of selections for the user to enter
        """
        options = []

        for index, selection in enumerate(selections):
            options.append(
                {
                    "selector": str(index + 1),
                    "prompt": selection,
                    "return": index + 1,
                }
            )
        return prompt.options(message, options)

    def run_command(self, cmd, suppress_message=False, return_output=False, run_as_user=None):
        """
        Run a shell command and show the output as it runs
        """

        def read_stream(output):
            file_descriptor = output.fileno()
            file_flags = fcntl.fcntl(file_descriptor, fcntl.F_GETFL)
            fcntl.fcntl(file_descriptor, fcntl.F_SETFL, file_flags | os.O_NONBLOCK)
            try:
                data = output.read()
            except (TypeError, BlockingIOError):
                return ""
            if data is None:
                return ""
            # ``universal_newlines`` is intentionally not enabled on Popen so
            # that carriage returns survive intact (Python's universal newlines
            # mode otherwise rewrites every ``\r`` to ``\n``, which destroys
            # in-place progress updates like the ones ``pip`` and ``apt`` emit).
            # Decode here with ``errors="replace"`` so a stray non-UTF-8 byte
            # doesn't kill the whole run.
            return data.decode("utf-8", errors="replace")

        # Allow running as a different user if we are root
        if self.is_root() and run_as_user is not None:
            pw_record = pwd.getpwnam(run_as_user)
            env = os.environ.copy()
            env["HOME"] = pw_record.pw_dir
            env["LOGNAME"] = run_as_user
            env["USER"] = pw_record.pw_name

            def preexec():
                os.setgid(pw_record.pw_gid)
                os.setuid(pw_record.pw_uid)

        else:
            env = None
            preexec = None

        full_output = ""
        # Per-stream "are we at the start of a new line?" state so the group
        # prefix is emitted exactly once per logical line, even when a chunk
        # arrives split across reads or contains in-place updates ending in
        # ``\r`` (e.g. download progress bars).
        stream_state = {"stdout": True, "stderr": True}
        with subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            preexec_fn=preexec,  # noqa: PLW1509  # single-threaded use; see preexec docstring
        ) as proc:
            while proc.poll() is None:
                err = read_stream(proc.stderr)
                if err and not suppress_message:
                    stream_state["stderr"] = self._emit_stream_chunk(
                        err, kind="error", at_line_start=stream_state["stderr"]
                    )
                output = read_stream(proc.stdout)
                if output and not suppress_message:
                    stream_state["stdout"] = self._emit_stream_chunk(
                        output, kind="info", at_line_start=stream_state["stdout"]
                    )
                full_output += output
            # Drain anything that arrived between the last read and the
            # process exit so short-lived commands don't lose their output.
            err = read_stream(proc.stderr)
            if err and not suppress_message:
                stream_state["stderr"] = self._emit_stream_chunk(
                    err, kind="error", at_line_start=stream_state["stderr"]
                )
            output = read_stream(proc.stdout)
            if output and not suppress_message:
                stream_state["stdout"] = self._emit_stream_chunk(
                    output, kind="info", at_line_start=stream_state["stdout"]
                )
            full_output += output
            return_code = proc.poll()
            proc.stdout.close()
            proc.stderr.close()
            if return_output:
                return full_output
            if return_code:
                return False
            return True

    # Match either ``\n`` (advance to next line) or one-or-more ``\r``
    # (return cursor to column 0). The two are handled differently:
    # ``\n`` starts a fresh logical line and gets a new group prefix,
    # while ``\r`` is treated as in-place cursor motion for things like
    # progress bars and does NOT re-emit the prefix -- subsequent redraw
    # frames overwrite the previous frame on the same visual line, prefix
    # included, which is how pip/apt progress UIs are designed to render.
    # Runs of ``\r`` are coalesced so patterns like ``\r\r%5d\r`` aren't
    # decomposed into separate redraw events.
    _LINE_BOUNDARY_RE = re.compile(r"\n|\r+")

    def _emit_stream_chunk(self, chunk, *, kind, at_line_start):
        """
        Write a chunk read from a subprocess stream to stdout, preserving
        the process's own line terminators and prepending the colored
        group prefix at the start of each *new* logical line.

        Two kinds of boundary appear in the stream:

        * ``\n`` (newline) -- starts a fresh logical line. The group prefix
          is re-emitted before the next non-empty content so each line in a
          log reads ``PITFT <message>``.
        * ``\r`` (carriage return) or runs of ``\r`` -- returns the cursor
          to column 0 in place. The prefix is NOT re-emitted on bare ``\r``.
          On a real terminal this lets progress UIs (apt, pip, ...) animate
          in place: the first frame of the line is written with a prefix,
          subsequent ``\r``-redraw frames overwrite the visible characters
          (prefix included) so the terminal shows the latest frame without
          a stale prefix dangling at column 0. Any content that follows a
          bare ``\r`` (e.g. apt's "erase the progress line, then start the
          next status line") is still emitted without a prefix; the next
          real ``\n`` is what re-arms prefix emission.

        Leading horizontal whitespace right after a ``\n`` boundary is
        treated as padding (apt occasionally leaves a stray space after a
        ``\r``-clear sequence) and is suppressed before the prefix is
        written, so output reads as e.g. ``PITFT Selecting previously
        unselected package ...`` rather than ``PITFT  Selecting ...`` with
        stray indentation.

        ``kind`` selects the color used for the group prefix
        (``"info"`` -> green, ``"error"`` -> red). The ``end="\n\r"`` that the
        old code hardcoded is *not* added here; whatever terminators the
        underlying process emitted are passed through unchanged so that
        carriage-return-based progress lines update in place instead of
        scrolling.

        Returns the updated ``at_line_start`` state for the next call.
        """
        if not chunk:
            return at_line_start

        # The original implementation funneled both info and error chunks
        # through ``print()`` (i.e. stdout). Preserve that routing here -- only
        # the prefix color differs between the two streams.
        if kind == "error":
            colorize = colored.red
        else:
            colorize = colored.green
        stream = sys.stdout

        prefix = colorize(self._group) + " " if self._group is not None else ""

        # Walk the chunk segment-by-segment, where each segment is the run of
        # bytes between two consecutive line boundaries (``\n`` or ``\r+``).
        # The boundary itself is written after its preceding segment, and the
        # next segment is treated as the start of a fresh logical line.
        pos = 0
        for match in self._LINE_BOUNDARY_RE.finditer(chunk):
            body = chunk[pos : match.start()]
            boundary = match.group(0)
            self._write_logical_line(stream, prefix, body, at_line_start, terminator=boundary)
            # Only ``\n`` resets the "start of logical line" state; bare
            # ``\r`` keeps the current line's continuation flag so any
            # following redraw content is written without a fresh prefix.
            at_line_start = boundary[0] == "\n"
            pos = match.end()
        # Anything after the last boundary is an unterminated tail; emit it
        # with no terminator. If we were at a line start and the tail was
        # pure leading whitespace that we suppressed, stay at line-start so
        # the prefix gets emitted with the real content on the next chunk.
        tail = chunk[pos:]
        if tail:
            wrote = self._write_logical_line(stream, prefix, tail, at_line_start, terminator="")
            if wrote:
                at_line_start = False
            # else: nothing was actually written (pure padding swallowed);
            # leave ``at_line_start`` as-is so the next chunk still gets
            # the prefix on its first real content.
        stream.flush()
        return at_line_start

    @staticmethod
    def _write_logical_line(stream, prefix, body, at_line_start, *, terminator):
        """Write one segment (optionally prefixed, optionally terminated).

        If ``at_line_start`` is true and a prefix is configured, the prefix
        is written first, then ``body`` with any leading horizontal
        whitespace stripped (so apt/pip padding doesn't push the real content
        to the right). If ``body`` is empty after stripping, the prefix is
        still suppressed so we don't leave a dangling ``PITFT `` on a
        whitespace-only "clear" line.

        Returns True if any body bytes were written (i.e. real content
        landed on this logical line). The terminator is written regardless,
        but does not count as body content -- callers use the return value
        to decide whether to flip ``at_line_start``.
        """
        wrote_body = False
        if at_line_start:
            # Strip leading horizontal whitespace (spaces/tabs) that the
            # source process used as padding. Don't strip ``\r`` / ``\n`` --
            # the regex already consumed those.
            stripped = body.lstrip(" \t")
            if stripped:
                if prefix:
                    stream.write(prefix)
                stream.write(stripped)
                wrote_body = True
            # else: pure-whitespace segment, swallow it; the terminator
            # below (likely ``\r``) still gets written so the terminal
            # still sees the cursor return.
        elif body:
            stream.write(body)
            wrote_body = True
        if terminator:
            stream.write(terminator)
        return wrote_body

    def write_templated_file(self, output_path, template, **kwargs):
        """
        Use a template file and render it with the given context and write it to the specified path.
        The template file should contain placeholders in the format {key} which will be replaced
        with the corresponding values from the kwargs dictionary.
        """
        # if path is an existing directory, the template filename will be used
        output_path = self.path(output_path)
        if os.path.isdir(output_path):
            output_path = os.path.join(output_path, os.path.basename(template))

        # Render the template with the provided context
        rendered_content = self.load_template(template, **kwargs)

        if rendered_content is None:
            self.error(
                f"Failed to load template '{template}'. Unable to write file '{output_path}'."
            )
            return False

        append = kwargs.get("append", False)
        self.write_text_file(output_path, rendered_content, append=append)

        return True

    def load_template(self, template, **kwargs):
        """
        Load a template file and return its content with the placeholders replaced by the provided
        context. The template file should contain placeholders in the format {key} which will be
        replaced with the corresponding values from the kwargs dictionary.
        """
        if not os.path.exists(template):
            self.error(f"Template file '{template}' does not exist")
            return None

        with open(template) as template_file:
            template_content = template_file.read()

        # Render the template with the provided context
        for key, value in kwargs.items():
            template_content = template_content.replace(f"{{{key}}}", str(value))

        return template_content

    def info(self, message, **kwargs):
        """
        Display a message with the group in green
        """
        if self._group is not None:
            print(colored.green(self._group) + " " + message, **kwargs)
        else:
            print(message, **kwargs)

    def warn(self, message, **kwargs):
        """
        Display a message with the group in yellow
        """
        if self._group is not None:
            print(colored.yellow(self._group) + " " + message, **kwargs)
        else:
            print(message, **kwargs)

    def bail(self, message=None, **kwargs):
        """
        Exit and display an error message if given
        """
        if message is None:
            self.error("Exiting due to error", **kwargs)
        else:
            self.error(f"Exiting due to error: {message}", **kwargs)
        sys.exit(1)

    def error(self, message, **kwargs):
        """
        Display some information
        """
        if self._group is not None:
            print(colored.red(self._group) + " " + message, **kwargs)
        else:
            print(message, **kwargs)

    @staticmethod
    def print_colored(message, color):
        """Print out a message in a specific color"""
        colors = ("red", "green", "yellow", "blue", "black", "magenta", "cyan", "white")
        if color in colors:
            colorize = getattr(colored, color)
            print(colorize(message))

    def prompt(self, message, *, default=None, force_arg=None, force_arg_value=True):
        """
        A Yes/No prompt that accepts optional defaults
        Returns True for Yes and False for No
        """
        if force_arg is not None and self.argument_exists(force_arg):
            return force_arg_value
        if default is None:
            choicebox = "[y/n]"
        else:
            if default not in {"y", "n"}:
                default = "y"
            choicebox = "[Y/n]" if default == "y" else "[y/N]"
        while True:
            reply = input(message + " " + choicebox + " ").strip()

            if not reply and default is not None:
                return default == "y"

            if re.match("y(?:es)?", reply, re.I):
                return True

            if re.match("n(?:o)?", reply, re.I):
                return False

    @staticmethod
    def clear():
        """
        Clear the screen.

        On an interactive TTY with a usable ``TERM``, defer to the
        ``clear`` binary so the user gets a real terminal reset
        (including scrollback flush where the emulator supports it).

        When stdout isn't a TTY (output piped or redirected, CI, etc.)
        or ``TERM`` is missing/unknown (``sudo`` with a stripped
        environment, ``env -i``, ...), do nothing. Clearing has no
        meaning when there's no screen to clear, and unconditionally
        shelling out to ``clear`` in those cases prints ncurses'
        ``'unknown': I need something more specific.`` to stderr.
        """
        if not sys.stdout.isatty():
            return
        term = os.environ.get("TERM", "")
        if not term or term in {"dumb", "unknown"}:
            return
        os.system("clear")

    @staticmethod
    def reboot():
        """
        Reboot the system
        """
        os.system("reboot")

    @staticmethod
    def getcwd():
        """
        Get the Current Working Directory
        """
        return os.getcwd()

    def chdir(self, directory):
        """
        Change directory
        """
        # if directory[0] != "/" and directory[0] != ".":
        #    directory = self.getcwd() + "/" + directory
        directory = self.path(directory)
        if not self.exists(directory):
            raise ValueError(f"Directory '{directory}' does not exist")
        if not self.isdir(directory):
            raise ValueError(f"The given location '{directory}' is not a directory")
        os.chdir(directory)

    def pushd(self, directory):
        """
        Change directory
        """
        # Add current dir to stack
        self._dirstack.append(self.getcwd())
        # change dir
        self.chdir(directory)

    def popd(self):
        """
        Change directory
        """
        # Add current dir to stack
        if len(self._dirstack) > 0:
            directory = self._dirstack.pop()
            self.chdir(directory)
        else:
            raise RuntimeError("Directory stack empty")

    @staticmethod
    def path(file_path):
        """
        Return the relative path. This works for paths starting with ~
        """
        return os.path.expanduser(file_path)

    @staticmethod
    def home_dir():
        """
        Return the User's home directory
        """
        return os.path.expanduser("~")

    @staticmethod
    def is_root():
        """
        Return whether the current user is logged in as root or has super user access
        """
        return os.geteuid() == 0

    @staticmethod
    def script():
        """
        Return the name of the script that is running
        """
        return sys.argv[0]

    def grep(self, search_term, location):
        """
        Run the grep command and return the result
        """
        location = self.path(location)
        return self.run_command(f"grep {search_term} {location}", suppress_message=True)

    @staticmethod
    def date():
        """
        Return a string containing the current date and time
        """
        return datetime.now().ctime()

    def reconfig(self, file, pattern, replacement):
        """
        Given a filename, a regex pattern to match and a replacement string,
        perform replacement if found, else append replacement to end of file.
        """
        if not self.isdir(file):
            if self.pattern_search(file, pattern):
                # Pattern found; replace in file
                self.pattern_replace(file, pattern, replacement)
            else:
                # Not found; append (silently)
                self.write_text_file(file, replacement, append=True)

    # pylint: disable=too-many-arguments
    def pattern_search(
        self, location, pattern, multi_line=False, return_match=False, find_all=False
    ):
        """
        Similar to grep, but uses pure python
        multi_line will search the entire file as a large text glob,
        but certain regex patterns such as ^ and $ will not work on a
        line-by-line basis
        returns True/False if found
        """
        location = self.path(location)
        found = False
        search_function = re.findall if find_all else re.search

        if self.exists(location) and not self.isdir(location):
            if multi_line:
                with open(location, "r+", encoding="utf-8") as file:
                    match = search_function(pattern, file.read(), flags=re.DOTALL)
                    if match:
                        found = True
            else:
                for line in fileinput.FileInput(location):
                    match = search_function(pattern, line)
                    if match:
                        found = True
                        break
        if return_match:
            return match
        return found

    def pattern_replace(self, location, pattern, replace="", multi_line=False):
        """
        Similar to sed, but uses pure python
        multi_line will search the entire file as a large text glob,
        but certain regex patterns such as ^ and $ will not work on a
        line-by-line basis
        """
        location = self.path(location)
        if self.pattern_search(location, pattern, multi_line):
            if multi_line:
                regex = re.compile(pattern, flags=re.DOTALL)
                with open(location, "r+", encoding="utf-8") as file:
                    data = file.read()
                    file.seek(0)
                    file.write(regex.sub(replace, data))
                    file.truncate()
                    file.close()
            else:
                regex = re.compile(pattern)
                for line in fileinput.FileInput(location, inplace=True):
                    if re.search(pattern, line):
                        print(regex.sub(replace, line), end="")
                    else:
                        print(line, end="")

    def isdir(self, location):
        """
        Check if a location exists and is a directory
        """
        location = self.path(location)
        return os.path.exists(location) and os.path.isdir(location)

    def exists(self, location):
        """
        Check if a path or file exists
        """
        location = self.path(location)
        return os.path.exists(location)

    def move(self, source, destination):
        """
        Move a file or directory from source to destination
        """
        source = self.path(source)
        destination = self.path(destination)
        if os.path.exists(source):
            if not os.path.isdir(source) and os.path.isdir(destination):
                destination += os.sep + os.path.basename(source)
            shutil.move(source, destination)

    def copy(self, source, destination):
        """
        Move a file or directory from source to destination
        """
        source = self.path(source)
        destination = self.path(destination)
        if os.path.exists(source):
            if os.path.isdir(source):
                shutil.copytree(source, destination)
            else:
                if os.path.isdir(destination):
                    destination += os.sep + os.path.basename(source)
                shutil.copy(source, destination)

    def chmod(self, location, mode):
        """
        Change the permissions of a file or directory
        """
        location = self.path(location)
        # Convert a text mode to an integer mode
        if isinstance(mode, str):
            if mode not in FILE_MODES:
                raise ValueError(f"Invalid mode string '{mode}'")
            mode = FILE_MODES[mode]
        if not 0 <= mode <= 0o777:
            raise ValueError(f"Invalid mode value '{mode}'")
        if os.path.exists(location):
            os.chmod(location, mode)

    def chown(self, location, user, group=None, recursive=False):
        """
        Change the owner of a file or directory
        """
        if group is None:
            group = user

        location = self.path(location)
        if recursive and os.path.isdir(location):
            for root, dirs, files in os.walk(location):
                for directory in dirs:
                    shutil.chown(
                        os.path.join(root, directory),
                        user,
                        group,
                    )
                for file in files:
                    shutil.chown(os.path.join(root, file), user, group)
        else:
            shutil.chown(location, user, group)

    def remove(self, location):
        """
        Remove a file or directory if it exists
        """
        location = self.path(location)
        if os.path.exists(location):
            if os.path.isdir(location):
                shutil.rmtree(location)
            else:
                os.remove(location)

    def require_root(self):
        """
        Check if the current user has root access and exit if not.
        """
        if not self.is_root():
            print("Installer must be run as root.")
            print(f"Try 'sudo python3 {self.script()}'")
            sys.exit(1)

    def write_text_file(self, path, content, append=True):
        """
        Write the contents to a file at the specified path
        """
        if append:
            mode = "a"
            content = "\n" + content
        else:
            mode = "w"
        with open(self.path(path), mode, encoding="utf-8") as service_file:
            service_file.write(content)

    def read_text_file(self, path):
        """
        Read the contents of a file at the specified path
        """
        path = self.path(path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"File '{path}' does not exist")
        with open(path, encoding="utf-8") as file:
            return file.read()

    @staticmethod
    def is_python3():
        "Check if we are running Python 3 or later"
        return int(platform.python_version()[0]) >= 3

    @staticmethod
    def is_linux():
        """
        Check that we are running linux
        """
        return platform.system() == "Linux" or platform.system() == "Darwin"

    @staticmethod
    def is_armhf():
        """
        Check if Platform.machine() (same as uname -m) returns an ARM platform that
        supports hardware floating point
        """
        return bool(re.match("armv.l", platform.machine()))

    @staticmethod
    def is_armv6():
        """
        Check if Platform.machine() returns ARM v6
        """
        return platform.machine() == "armv6l"

    @staticmethod
    def is_armv7():
        """
        Check if Platform.machine() returns ARM v7
        """
        return platform.machine() == "armv7l"

    @staticmethod
    def is_armv8():
        """
        Check if Platform.machine() returns ARM v8
        """
        return platform.machine() == "armv8l"

    @staticmethod
    def is_arm64():
        """
        Check if Platform.machine() returns ARM 64
        """
        return platform.machine() == "aarch64"

    @staticmethod
    def get_arch():
        """Return a string containing the architecture"""
        return platform.machine()

    # pylint: disable=invalid-name
    def get_os(self):
        """Return a string containing the release which we can use to compare in the script"""
        os_releases = (
            "Raspbian",
            "Debian",
            "Kano",
            "Mate",
            "PiTop",
            "Ubuntu",
            "Darwin",
            "Kali",
        )
        release = None
        if os.path.exists("/etc/os-release"):
            with open("/etc/os-release", encoding="utf-8") as f:
                if "Raspbian" in f.read():
                    release = "Raspbian"
            if self.exists("/etc/rpi-issue"):
                with open("/etc/rpi-issue", encoding="utf-8") as f:
                    if "Raspberry Pi" in f.read():
                        release = "Raspbian"
            if self.run_command("command -v apt-get", suppress_message=True):
                with open("/etc/os-release", encoding="utf-8") as f:
                    release_file = f.read()
                    for opsys in os_releases:
                        if opsys in release_file:
                            release = opsys
                if release == "Debian" and os.path.exists("/etc/rpi-issue"):
                    release = "Raspbian"
        if self.isdir("/etc/pi-top") or self.isdir("~/.config/pi-top"):
            release = "PiTop"
        if self.isdir("~/.kano-settings") or self.isdir("~/.kanoprofile"):
            release = "Kano"
        if self.isdir("~/.config/ubuntu-mate"):
            release = "Mate"
        if platform.system() == "Darwin":
            release = "Darwin"
        return release

    def get_raspbian_version(self):
        """Return a string containing the raspbian version"""
        if self.get_os() != "Raspbian":
            return None
        if os.path.exists("/etc/os-release"):
            with open("/etc/os-release", encoding="utf-8") as f:
                release_file = f.read()
                if "/sid" in release_file:
                    return "unstable"
                for raspbian in RASPI_VERSIONS:
                    if raspbian in release_file:
                        return raspbian
        return None

    def is_minimum_version(self, version):
        """Check if the version is at least the specified version"""
        # Check that version is a string
        if not isinstance(version, str):
            raise ValueError("Version must be a string")
        # Check that version is in the list of valid versions
        if version.lower() not in RASPI_VERSIONS:
            raise ValueError("Invalid version")
        # Check that the current version is at least the specified version
        return RASPI_VERSIONS.index(self.get_raspbian_version()) >= RASPI_VERSIONS.index(
            version.lower()
        )

    def prompt_reboot(self, default="y", **kwargs):
        """Prompt the user for a reboot"""
        if not self.prompt("REBOOT NOW?", default=default, **kwargs):
            print("Exiting without reboot.")
        else:
            print("Reboot started...")
            os.sync()
            self.reboot()
        self.exit()

    def check_kernel_update_reboot_required(self):
        """Checks if the pi needs to be rebooted since the last kernel update"""
        if not self.exists(f"/lib/modules/{self.release()}"):
            self.error(
                "OS has not been rebooted since last kernel update. "
                "Please reboot and re-run the script."
            )
            self.prompt_reboot()

    def check_kernel_userspace_mismatch(self, attempt_fix=True, fix_with_x11=False):
        """
        Check if the userspace is 64-bit and kernel is 32-bit
        """
        if self.is_kernel_userspace_mismatched():
            print(
                "Unable to compile driver because kernel space is 64-bit, but user space is 32-bit."
            )
            config = self.get_boot_config()
            if (
                self.is_raspberry_pi_os()
                and attempt_fix
                and config
                and self.prompt(f"Add parameter to {config} to use 32-bit kernel?")
            ):
                # Set to use 32-bit kernel
                self.reconfig(config, "^.*arm_64bit.*$", "arm_64bit=0")
                if fix_with_x11:
                    self.set_window_manager("x11")
                self.prompt_reboot()
            else:
                raise RuntimeError("Unable to continue while mismatch is present.")

    def run_raspi_config(self, args, suppress_message=False, return_output=False, run_as_user=None):
        """
        Run a ``raspi-config nonint ...`` command, but only on Raspberry Pi OS.

        ``raspi-config`` is only shipped (and only honored) on Raspberry Pi OS;
        on other distros (DietPi, Ubuntu, etc.) the binary is absent and the
        tweak would not apply anyway. On non-Pi-OS systems this method is a
        no-op that returns ``True`` (or ``""`` when ``return_output=True``)
        so existing call sites continue to work without producing misleading
        "command not found" output.

        ``args`` is the part after ``raspi-config nonint`` (e.g. ``"do_spi 0"``).
        ``suppress_message``, ``return_output``, and ``run_as_user`` are
        forwarded to :meth:`run_command`.
        """
        if not self.is_raspberry_pi_os():
            return "" if return_output else True
        return self.run_command(
            "raspi-config nonint " + args,
            suppress_message=suppress_message,
            return_output=return_output,
            run_as_user=run_as_user,
        )

    def set_window_manager(self, manager):
        """
        Call raspi-config to set a new window manager
        """
        if not self.is_minimum_version("bullseye"):
            return

        if manager.lower() not in WINDOW_MANAGERS:
            raise ValueError("Invalid window manager")

        if manager.lower() == "labwc" and not self.exists("/usr/bin/labwc"):
            raise RuntimeError("labwc is not installed")

        print(f"Using {manager} as the window manager")
        if not self.run_raspi_config("do_wayland " + WINDOW_MANAGERS[manager.lower()]):
            raise RuntimeError("Unable to change window manager")

    def get_window_manager(self):
        """
        Get the current window manager
        """
        sessions = {"wayfire": "LXDE-pi-wayfire"}
        # Check for Raspbian Desktop sessions
        if self.exists("/usr/share/xsessions/rpd-x.desktop") or self.exists(
            "/usr/share/wayland-sessions/rpd-labwc.desktop"
        ):
            sessions.update({"x11": "rpd-x", "labwc": "rpd-labwc"})
        else:
            sessions.update({"x11": "LXDE-pi-x", "labwc": "LXDE-pi-labwc"})

        matches = self.pattern_search(
            "/etc/lightdm/lightdm.conf", "^(?!#.*?)user-session=(.+)", False, True
        )
        if matches:
            session_match = matches.group(1)
            for key, session in sessions.items():
                if session_match == session:
                    return key
        return None

    def get_boot_config(self):
        """
        Get the location of the boot config file
        """
        # check if /boot/firmware/config.txt exists
        if self.exists("/boot/firmware/config.txt"):
            return "/boot/firmware/config.txt"
        if self.exists("/boot/config.txt"):
            return "/boot/config.txt"
        return None

    def is_kernel_userspace_mismatched(self):
        """
        If the userspace 64-bit and kernel is 32-bit?
        """
        return self.is_arm64() and platform.architecture()[0] == "32bit"

    # pylint: enable=invalid-name

    def is_raspberry_pi_os(self):
        """
        Check if we are running Raspberry Pi OS or Raspbian
        """
        return self.get_os() == "Raspbian"

    @staticmethod
    def is_raspberry_pi():
        """
        Use PlatformDetect to check if this is a Raspberry Pi
        """
        detector = adafruit_platformdetect.Detector()
        return detector.board.any_raspberry_pi

    @staticmethod
    def get_board_model():
        """
        Use PlatformDetect to get the board model
        """
        detector = adafruit_platformdetect.Detector()
        return detector.board.id

    @staticmethod
    def is_pi5_or_newer():
        """
        Use PlatformDetect to check if this is a Raspberry Pi 5 or newer
        """
        detector = adafruit_platformdetect.Detector()
        return detector.board.any_raspberry_pi_5_board

    @staticmethod
    def get_architecture():
        """
        Get the type of Processor
        """
        return platform.machine()

    @staticmethod
    def kernel_minimum(version):
        """
        Check that we are running on at least the specified version
        """
        return platform.release() >= str(version)

    @staticmethod
    def release():
        """
        Return the latest kernel release version
        """
        return platform.release()

    def argument_exists(self, arg, prefix="-"):
        """
        Check if the given argument was supplied
        """
        return prefix + arg in self.args

    @staticmethod
    def exit(status_code=0):
        """
        Exit and return the status code to the OS
        """
        sys.exit(status_code)

    @property
    def group(self):
        """
        Get or set the current group that is displayed in color along with messages
        """
        return self._group

    @group.setter
    def group(self, value):
        self._group = str(value)

    @property
    def args(self):
        """
        Get a list of supplied arguments
        """
        return sys.argv
