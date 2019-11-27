import logging
import os
import re
import shlex
import sys

class Cluster(object):
    def __init__(self, SSHManager):
        """
        Setup the logger
        """
        self.log = logging.getLogger(__name__)
        self.ssh = SSHManager

    def resolve_path(self,path):
        """
        Evaluate path on the remote side
        """

        if path == '.':
            out, _ = self.ssh.remote_cmd("echo $HOME")
            self.log.debug("$HOME is %s" % home)
        else:
            out, _ = self.ssh.remote_cmd("eval echo %s" % path)

        self.log.debug("transformed path %s to %s" % (path, out))

        return out


    def resolve_platform(self):
        """
        Try to identify the remote platform. Currently RH+variants/Debian/Ubuntu
        are supported. A lot of code was lifted from the 'blivet' lib which
        implicitly GPL-ifies this
        """
        search_path = ['/etc/os-release','/etc/redhat-release']
        for path in search_path:
            try:
                self.ssh.sftp.lstat(path)
                f = path
                break
            except IOError:
                f = None
                self.log.debug("Couldn't open %s, continuing.." % path)
        if f is None:
            self.log.error("Unknown or unsupported distribution")
            sys.exit(1)

        if 'os-release' in f:
            self.log.debug("Parsing os-release")
            with self.ssh.sftp.file(f) as fh:
                    parser = shlex.shlex(fh)
                    while True:
                        key = parser.get_token()
                        if key == parser.eof:
                            break
                        elif key == "NAME":
                            # Throw away the "=".
                            parser.get_token()
                            relName = parser.get_token().strip("'\"")
                            self.log.debug(relName)
                        elif key == "VERSION_ID":
                            # Throw away the "=".
                            parser.get_token()
                            version = parser.get_token().strip("'\"")
                            relVer = version.split()[0].split(".",1)[0]
                            self.log.debug(relVer)

        elif 'redhat-release' in f:
            self.log.debug("Parsing redhat-release")
            with self.ssh.sftp.file(f) as fh:
                relstr = fh.readline().strip()
            (product, sep, version) = relstr.partition(" release ")
            if sep:
                relName = product
                relVer = version.split()[0].split(".",1)[0]

        self.log.info("Remote distribution and version is: %s %s" % (relName, relVer))

        # assume Linux for now
        if any(map(lambda(p): re.search(p,relName, re.I), ["red ?hat", "scientific", "centos"])):
            distro = "RedHat" + relVer
        elif relName in ["Debian"]:
            distro = "Debian" + relVer
        elif relName in ["Ubuntu"]:
            distro = "Ubuntu" + relVer

        return distro
