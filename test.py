try:
    from adafruit_shell import Shell
except ImportError:
    raise RuntimeError(
        "The library 'adafruit_shell' was not found. To install, try typing: sudo pip3 install adafruit-python-shell"
    )

shell = Shell()
shell.group = "Blinka"


def main():
    # shell.clear()
    print("Running test")
    shell.run_command("./test.sh")


# Main function
if __name__ == "__main__":
    main()
