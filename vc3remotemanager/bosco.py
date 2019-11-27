from ftplib import FTP
from tarfile import TarFile

import errno
import logging
import os
import re
import shutil
import sys
import tarfile
import tempfile
import textwrap

try:
    from urllib.parse import urlparse
except ImportError:
     from urlparse import urlparse

class Bosco(object):
    def __init__(self, **kwargs):
        self.cluster     = kwargs.get('Cluster', None)
        self.ssh         = kwargs.get('SSHManager', None)
        self.lrms        = kwargs.get('lrms', None)
        self.version     = kwargs.get('version', "1.2.10")
        self.repository  = kwargs.get('repository', "ftp://ftp.cs.wisc.edu/condor/bosco")
        self.tag         = kwargs.get('tag', None)
        self.cachedir    = kwargs.get('cachedir', "/tmp/bosco")
        self.sandbox     = kwargs.get('sandbox', None)
        self.installdir  = kwargs.get('installdir','~/.condor') #overwrite this later
        self.patchset    = kwargs.get('patchset', None)
        self.rdistro     = kwargs.get('rdistro', None)
        self.clusterlist = kwargs.get('clusterlist', None)
        self.log         = logging.getLogger(__name__)

        try:
            self.installdir = self.cluster.resolve_path(self.installdir) # is this bad?
            self.log.debug("Installdir is %s" % self.installdir)
        except Exception as e:
            self.log.warn("Couldn't resolve installdir.. %s" % e)
            sys.exit(1) # we should probably cowardly bail out here
            #self.installdir = installdir

            self.clusterlist = os.path.join(self.cachedir, ".clusterlist")

        if self.sandbox is None:
            self.sandbox = os.path.join(self.installdir,"bosco/sandbox")
            self.log.debug("Sandbox directory not specified, defaulting to %s" % self.sandbox)
        else:
            self.log.debug("Sandbox directory is %s" % self.sandbox)

        if self.clusterlist is None:
            self.clusterlist = os.path.join(self.cachedir, ".clusterlist")

        if self.lrms is None:
            self.log.debug("Missing required option lrms: %s" % self.lrms)
        if self.cluster is None:
            self.log.debug("Missing required option Cluster: %s" % self.cluster)
        if self.ssh is None:
            self.log.debug("Missing required option SSHManager: %s" % self.ssh)

        self.etcdir = self.installdir + "/bosco/glite/etc"

    def cache_tarballs(self):
        r = urlparse(self.repository)
        path = r.path + "/" + self.version
        self.log.debug("repo is %s " % r.netloc)
        self.log.debug("path is %s " % path)

        ftp = FTP(r.netloc)
        ftp.login()
        ftp.cwd(path)
        files = ftp.nlst()
        match = "bosco-"
        tarballs = [s for s in files if re.match(match, s)]

        dldir = os.path.join(self.cachedir, self.version)

        try:
            os.makedirs(dldir)
        except OSError, e:
            if e.errno != errno.EEXIST:
                raise

        # compare what we have on disk to upstream
        to_dl = set(tarballs) - set(os.listdir(dldir))

        if to_dl:
            self.log.info("Caching missing tarballs: %s" % ", ".join(to_dl))
            for tar in to_dl:
                fn = os.path.join(dldir, tar)
                with open(fn, 'wb') as f:
                    self.log.debug("Downloading.. %s" % tar)
                    ftp.retrbinary('RETR ' + tar, f.write)
        else:
            self.log.debug("Nothing to download, continuing..")

        ftp.close()

    def extract_blahp(self, distro):
        """
        Extract the BLAHP shared libs and bins for the target platform and dump
        them to a temporary directory
        """
        tempdir = tempfile.mkdtemp()
        self.log.debug("Temporary working directory: %s" % tempdir)

        tarfile = os.path.join(self.cachedir,self.version,"bosco-1.2-x86_64_" + distro + ".tar.gz")

        cdir = 'condor-8.6.6-x86_64_' + distro + '-stripped/'

        blahp_files = [
            'lib/libclassad.so.8.6.6',
            'lib/libclassad.so.8',
            'lib/libclassad.so',
            'lib/libcondor_utils_8_6_6.so',
            'sbin/condor_ft-gahp' ]
        blahp_dirs = [
            'lib/condor/',
            'libexec/glite/bin',
            'libexec/glite/etc' ]

        # open the tarball, extract blahp_files and blahp_dirs to tmp
        with TarFile.open(tarfile) as t:
            members = []
            for f in blahp_files:
                members.append(t.getmember(os.path.join(cdir,f)))
            for d in blahp_dirs:
                match = os.path.join(cdir, d)
                files = [t.getmember(s) for s in t.getnames() if re.match(match, s)]
                members.extend(files)

            #self.log.debug("Extracting %s to %s" % (members, tempdir))
            t.extractall(tempdir,members)

        # once things are in tmp, we need to need to move things around and
        # make some directories
        dirs = [ 'bosco/glite/log', 'bosco/sandbox' ]
        self.log.debug("Creating BOSCO directories...")
        for d in dirs:
            os.makedirs(os.path.join(tempdir, d))

        # list of files and directories that need to move from the extracted tarball to the bosco dir
        to_move = (
            ['lib','bosco/glite/lib'],
            ['libexec/glite/bin', 'bosco/glite/bin'],
            ['libexec/glite/etc', 'bosco/glite/etc'],
            ['sbin/condor_ft-gahp', 'bosco/glite/bin/condor_ft-gahp'] )

        for t in to_move:
            src = os.path.join(tempdir,cdir,t[0])
            dst = os.path.join(tempdir,t[1])
            self.log.debug("Moving %s to %s" % (src,dst))
            shutil.move(src,dst)

        self.log.debug("Deleting old directory: %s " % cdir)
        shutil.rmtree(os.path.join(tempdir,cdir))

        return tempdir

    def create_tarball(self, dst, src):
        outfile = dst + ".tar.gz"
        with tarfile.open(outfile, "w:gz") as tar:
            tar.add(src, arcname=os.path.basename(src))
        return outfile

    def config_ft_gahp(self):
        #cat >$remote_glite_dir/etc/condor_config.ft-gahp 2>/dev/null <<EOF
        #BOSCO_SANDBOX_DIR=\$ENV(HOME)/$remote_sandbox_dir
        #LOG=\$ENV(HOME)/$remote_base_dir_host/glite/log
        #FT_GAHP_LOG=\$(LOG)/FTGahpLog
        #SEC_CLIENT_AUTHENTICATION_METHODS = FS, PASSWORD
        #SEC_PASSWORD_FILE = \$ENV(HOME)/$remote_base_dir_host/glite/etc/passwdfile
        #USE_SHARED_PORT = False
        #ENABLE_URL_TRANSFERS = False
        #EOF

        installpath = self.cluster.resolve_path(self.installdir)
        sandboxpath = self.cluster.resolve_path(self.sandbox)

        config = """\
            BOSCO_SANDBOX_DIR=%s
            LOG=%s/bosco/glite/log
            FT_GAHP_LOG=$(LOG)/FTGahpLog
            SEC_CLIENT_AUTHENTICATION_METHODS = FS, PASSWORD
            SEC_PASSWORD_FILE = %s/bosco/glite/etc/passwdfile
            USE_SHARED_PORT = False
            ENABLE_URL_TRANSFERS = False
        """ % (sandboxpath, installpath, installpath)

        c = textwrap.dedent(config)
        cfgfile = os.path.join(self.etcdir,"condor_config.ft-gahp")
        self.log.info("Writing HTCondor File Transfer GAHP config file %s" % cfgfile)
        with self.ssh.sftp.open(cfgfile, 'wb') as f:
            f.write(c)

    def apply_patches(self, tempdir):
        """
        Apply patches to address resource-specific quirks.
        """
        self.log.info("Applying patch set %s to installation on %s ..." % (self.patchset, self.ssh.host))
        # after a hard think, we'll just replace the files on the remote side
        # instead of using patch(1).
        r = os.path.abspath(os.path.dirname(__file__))
        patchdir = os.path.join(r, '../patches') # this seems brittle?

        # we also need the specific patches for this version of bosco
        # and the particular resource we're applying a patch against
        p = os.path.abspath(os.path.join(patchdir,self.version,self.patchset))
        self.log.debug("Fully formed patch path is: %s" % p)
        try:
            os.stat(p)
            t = self.create_tarball(os.path.join(tempdir,self.patchset), os.path.join(p,"glite"))
            dst = self.cluster.resolve_path(self.installdir + "/bosco/") + os.path.basename(t)

            self.log.debug("Source is %s, Destination is %s" % (t,dst))
            try:
                self.ssh.sftp.put(t, dst)
                _, err =self.ssh.remote_cmd("tar -xzf " + dst + " -C " + self.installdir + "/bosco" )
                if err is not '':
                    self.log.debug(err)
            except:
                self.log.debug("Couldn't transfer %s to %s!" % (t, dst))

            self.log.info("Deleting temporary file %s" % dst)
            self.ssh.sftp.remove(dst)

        except OSError:
            self.log.debug("Couldn't open the patchset %s, something probably went wrong..." % p)

    def setup_bosco(self):
        self.log.info("Retrieving BOSCO tarballs from FTP...")
        self.cache_tarballs()

        if self.rdistro is not None:
            distro = self.rdistro
        else:
            self.log.debug("No distro override configured, proceeding as normal...")
            distro = self.cluster.resolve_platform()

        self.log.info("Extracting BOSCO files for platform %s" % distro)
        bdir = self.extract_blahp(distro)
        if self.tag is not None:
            tarname = "bosco" + "-" + self.tag
        else:
            tarname = "bosco"

        self.log.info("Creating new BOSCO tarball for target %s" % self.ssh.host)
        t = self.create_tarball(tarname, os.path.join(bdir,"bosco"))
        self.log.debug("t is %s" % t)

        src = os.path.join(os.getcwd(),t)
        dst = self.cluster.resolve_path(self.installdir + "/" + t)
        self.log.info("Transferring %s to %s" % (src, dst))
        try:
            self.ssh.sftp.mkdir(self.cluster.resolve_path(self.installdir))
        except IOError as e:
            self.log.debug("Couldn't create installdir.. perhaps it already exists?")
        try:
            self.ssh.sftp.put(src, dst)
        except Exception as e:
            self.log.error("Couldn't transfer %s to %s!" % (src, self.ssh.host + ":" + dst))
            self.log.debug(e)
            raise

        self.log.info("Extracting %s to %s" % ((self.ssh.host + ":" + dst),self.installdir))
        _, err = self.ssh.remote_cmd("tar -xzf " + dst + " -C " + self.installdir )
        if err is not '':
            self.log.debug(err)
        self.log.info("Deleting temporary file %s" % dst)
        self.ssh.sftp.remove(dst)

        # configure file transfer gahp daemon
        self.config_ft_gahp()

        # apply patches for the site
        if self.patchset is not None:
            self.apply_patches(bdir)
        else:
            self.log.debug("No patches to apply, moving on...")

        self.add_cluster()

        # cleanup tempfile
        self.log.info("Cleaning up tempdir %s" % bdir)
        shutil.rmtree(bdir)

    def add_cluster(self):
        openMode = 'a+'
        try:
            os.stat(self.clusterlist)
        except OSError:
            if os.path.isdir(self.cachedir) == False:
                os.mkdir(self.cachedir)
            openMode = 'w+'

        # entry=ruc.mwt2@mwt2-gk.campuscluster.illinois.edu max_queued=-1 cluster_type=condor
        with open(self.clusterlist, openMode) as f:
            # this isnt atomic so...
            clusters = self.get_clusters()

            # assemble the entry
            entry = self.ssh.login + "@" + self.ssh.host

            if entry in clusters:
                self.log.debug("already in cluster list. skipping duplicate entry")
            else:
                s = "entry=%s max_queueud=%d cluster_type=%s " % (entry, -1, self.lrms)
                self.log.info("Writing cluster entry %s to %s:" % (s, self.clusterlist))
                try:
                    f.write(s + "\n")
                except IOError:
                    self.log.debug("Couldn't write file.")

    def get_clusters(self):
        """
        return a dict of clusters
        """
        # example:
        # entry=ruc.mwt2@mwt2-gk.campuscluster.illinois.edu max_queued=-1 cluster_type=condor
        # return:
        # {'lincolnb@uct3-s1.mwt2.org':'condor', ...}
        openMode = 'r'
        try:
            os.stat(self.clusterlist)
        except OSError:
            if os.path.isdir(self.cachedir) == False:
                os.mkdir(self.cachedir)
            openMode = 'w+'

        with open(self.clusterlist, openMode) as f:
            clusters = f.readlines()
            group = {}
            for cluster in clusters:
                entry = cluster.split(' ')
                c = []
                for item in entry:
                    c += item.split('=')
                group[c[1]] = c[5]
        self.log.debug("Cluster dict is %s" % group)
        return group
