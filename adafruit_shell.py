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
import subprocess
import platform
from re import match, I
from clint.textui import colored, prompt

__version__ = "0.0.0-auto.0"
__repo__ = "https://github.com/adafruit/Adafruit_Python_Shell.git"


class Shell:
    """
    Class to help with converting Shell scripts over to Python. Having all
    the functions in one place makes updates easier and code shorter.
    """

    def __init__(self):
        self._group = None

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

    def run_command(self, cmd, suppress_message=False):
        """
        Run a shell command and show the output as it runs
        """
        proc = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        while True:
            output = proc.stdout.readline()
            if len(output) == 0 and proc.poll() is not None:
                break
            if output and not suppress_message:
                self.info(output.decode("utf-8").strip())
        r = proc.poll()
        if r == 0:
            return True

        err = proc.stderr.read()
        if not suppress_message:
            self.error(err.decode("utf-8"))
        return False

    def info(self, message):
        """
        Display some inforrmation
        """
        if self._group is not None:
            print(colored.green(self._group) + " " + message)
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
    def prompt(message, default=None):
        """
        A Yes/No prompt that accepts optional defaults
        Returns True for Yes and False for No
        """
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

            if match("y(?:es)?", reply, I):
                return True

            if match("n(?:o)?", reply, I):
                return False

    @staticmethod
    def clear():
        """
        Clear the screen
        """
        os.system("clear")

    @staticmethod
    def is_root():
        """
        Return whether the current user is logged in as root or has super user access
        """
        return os.geteuid() == 0

    def require_root(self):
        """
        Check if the current user has root access and exit if not.
        """
        if not self.is_root():
            print("Installer must be run as root.")
            print("Try 'sudo python3 {}'".format(sys.argv[0]))
            sys.exit(1)

    @staticmethod
    def write_text_file(path, content, append=True):
        """
        Write the contents to a file at the specified path
        """
        if append:
            mode = "a"
        else:
            mode = "w"
        service_file = open(path, mode)
        service_file.write(content)
        service_file.close()

    @staticmethod
    def is_linux():
        """
        Check that we are running linux
        """
        return platform.system() == "Linux"

    @staticmethod
    def kernel_minimum(version):
        """
        Check that we are running on at least the specified version
        """
        return platform.release() >= str(version)

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
