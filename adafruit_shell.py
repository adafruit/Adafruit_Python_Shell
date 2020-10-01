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
import sys
import os
import shutil
import subprocess
import platform
import fileinput
import re
from datetime import datetime
from clint.textui import colored, prompt
import adafruit_platformdetect

__version__ = "0.0.0-auto.0"
__repo__ = "https://github.com/adafruit/Adafruit_Python_Shell.git"

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
                {"selector": str(index + 1), "prompt": selection, "return": index + 1,}
            )
        return prompt.options(message, options)

    def run_command(self, cmd, suppress_message=False, return_output=False):
        """
        Run a shell command and show the output as it runs
        """
        proc = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        full_output = ""
        while True:
            output = proc.stdout.readline()
            if len(output) == 0 and proc.poll() is not None:
                break
            if output:
                decoded_output = output.decode("utf-8").strip()
                if not suppress_message:
                    self.info(decoded_output)
                full_output += decoded_output
        r = proc.poll()
        if r == 0:
            if return_output:
                return full_output
            return True

        err = proc.stderr.read()
        if not suppress_message:
            self.error(err.decode("utf-8"))
        if return_output:
            return full_output
        return False

    def info(self, message):
        """
        Display a message with the group in green
        """
        if self._group is not None:
            print(colored.green(self._group) + " " + message)
        else:
            print(message)

    def warn(self, message):
        """
        Display a message with the group in yellow
        """
        if self._group is not None:
            print(colored.yellow(self._group) + " " + message)
        else:
            print(message)

    def bail(self, message=None):
        """
        Exit and display an error message if given
        """
        if message is None:
            self.error("Exiting due to error")
        else:
            self.error("Exiting due to error: {}".format(message))
        sys.exit(1)

    def error(self, message):
        """
        Display some inforrmation
        """
        if self._group is not None:
            print(colored.red(self._group) + " " + message)
        else:
            print(message)

    @staticmethod
    def print_colored(message, color):
        """Print out a message in a specific color"""
        colors = ("red", "green", "yellow", "blue", "black", "magenta", "cyan", "white")
        if color in colors:
            colorize = getattr(colored, color)
            print(colorize(message))

    def prompt(self, message, *, default=None, force_arg=None):
        """
        A Yes/No prompt that accepts optional defaults
        Returns True for Yes and False for No
        """
        if force_arg is not None and self.argument_exists(force_arg):
            return True
        if default is None:
            choicebox = "[y/n]"
        else:
            if default not in ["y", "n"]:
                default = "y"
            choicebox = "[Y/n]" if default == "y" else "[y/N]"
        while True:
            reply = input(message + " " + choicebox + " ").strip()

            if reply == "" and default is not None:
                return default == "y"

            if re.match("y(?:es)?", reply, re.I):
                return True

            if re.match("n(?:o)?", reply, re.I):
                return False

    @staticmethod
    def clear():
        """
        Clear the screen
        """
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
            raise ValueError("Directory does not exist")
        if not self.isdir(directory):
            raise ValueError("Given location is not a directory")
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
        return self.run_command(
            "grep {} {}".format(search_term, location), suppress_message=True
        )

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

    def pattern_search(self, location, pattern, multi_line=False):
        """
        Similar to grep, but uses pure python
        multi_line will search the entire file as a large text glob,
        but certain regex patterns such as ^ and $ will not work on a
        line-by-line basis
        returns True/False if found
        """
        location = self.path(location)
        found = False

        if self.exists(location) and not self.isdir(location):
            if multi_line:
                with open(location, "r+") as file:
                    if re.search(pattern, file.read(), flags=re.DOTALL):
                        found = True
            else:
                for line in fileinput.FileInput(location):
                    if re.search(pattern, line):
                        found = True

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
                with open(location, "r+") as file:
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
            shutil.move(source, destination)

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
            print("Try 'sudo python3 {}'".format(self.script()))
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
        service_file = open(self.path(path), mode)
        service_file.write(content)
        service_file.close()

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
            with open("/etc/os-release") as f:
                if "Raspbian" in f.read():
                    release = "Raspian"
            if self.run_command("command -v apt-get", suppress_message=True):
                with open("/etc/os-release") as f:
                    release_file = f.read()
                    for opsys in os_releases:
                        if opsys in release_file:
                            release = opsys
        if os.path.isdir(os.path.expanduser("~/.kano-settings")) or os.path.isdir(
            os.path.expanduser("~/.kanoprofile")
        ):
            release = "Kano"
        if os.path.isdir(os.path.expanduser("~/.config/ubuntu-mate")):
            release = "Mate"
        if platform.system() == "Darwin":
            release = "Darwin"
        return release

    def get_raspbian_version(self):
        """Return a string containing the raspbian version"""
        if self.get_os() != "Raspbian":
            return None
        raspbian_releases = ("buster", "stretch", "jessie", "wheezy")
        if os.path.exists("/etc/os-release"):
            with open("/etc/os-release") as f:
                release_file = f.read()
                if "/sid" in release_file:
                    return "unstable"
                for raspbian in raspbian_releases:
                    if raspbian in release_file:
                        return raspbian
        return None

    # pylint: enable=invalid-name

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
