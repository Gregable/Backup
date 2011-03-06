"""
Automated Backup Script.
Mounts an encfs directory and rsyncs a series of files to that path.  Also
makes periodic snapshots of the encfs directory for reverting changes.
Configurable based on file located at $HOME/.backuprc.  Triggered by cron
commands, for example:
 * * * * * date > /home/greg/.heartbeat
 */15 * * * * python /home/greg/bin/backup.py snapshot
 0 * * * * python /home/greg/bin/backup.py hourly
 0 0 * * * python /home/greg/bin/backup.py daily
 0 0 * * 0 python /home/greg/bin/backup.py weekly
 0 0 1 * * python /home/greg/bin/backup.py monthly
"""
import logging
import os
import subprocess
import sys

# Modify this is you would like to put the config file anywhere other than
# $HOME/.backuprc
BACKUP_RC = os.path.join(os.environ['HOME'], '.backuprc')

class BackupConfig(object):
  def __init__(self, filename):
    assert os.path.exists(filename)
    self.FILEPATHS = []
    self.Process(filename)

  def Process(self, filename):
      # Config lines are one of:
      #   - "# Comment beginning with hash character"
      #   - "" <- empty line that is ignored
      #   - "KEY VALUE" pairs
      #   - "/directory/to/recursively/backup/" <- must end in "/"
      #   - "/directory/with_specific_file.to_backup"
    for line in open(filename).xreadlines():
      line = line.strip()
      # lines beginning with # are comments
      if not line or line[0] == '#': continue

      # Add the key value pairs to my own dictionary
      if ' ' in line:
        key, value = line.split(' ', 1)
        self.__dict__[key] = value
      else:
        self.FILEPATHS.append(line)

class ShellError(OSError):
  """Popen() returned non-0."""
  def __init__(self, command, cwd, returncode, stdout, stderr=None):
    OSError.__init__(self, command, cwd, returncode, stdout, stderr)
    self.command = command
    self.cwd = cwd
    self.returncode = returncode
    self.stdout = stdout
    self.stderr = stderr

  def PrintWithPrefix(self, to_print, prefix_len):
    out = ""
    for line in to_print.splitlines():
      out += (" " * prefix_len) + to_print
    return out

  def __str__(self):
    out = "Return Value: %d\n" % self.returncode
    if self.stdout:
      out += self.PrintWithPrefix("<Standard Out>", 2)
      out += self.PrintWithPrefix(self.stdout.read(), 4)
      out += self.PrintWithPrefix("</Standard Out>", 2)
    if self.stderr:
      out += self.PrintWithPrefix("<Standard Err>\n", 2)
      out += self.PrintWithPrefix(self.stderr, 4)
      out += self.PrintWithPrefix("</Standard Err>", 2)
    return out


def Shell(cmd, cwd=None):
  process = subprocess.Popen(cmd, cwd=cwd, shell=True,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
  if process.wait():
    stdout, stderr = process.communicate()
    raise ShellError(cmd, cwd, process.returncode, stdout, stderr)


def RunBackup(cfg):
  # Pre mount external filesystem if needed
  try:
    premount = cfg.PRE_MOUNT
  except AttributeError:
    pass  # No pre-mount specified, that's OK

  try:
    Shell("mount %s" % premount)
  except ShellError as e:
    if e.returncode != 32:  # 32 indicates already mounted
      logging.exception("Pre Mount Failed")
      raise

  # Mount an encrypted filesystem (encfs) on top of our backup mount point.
  current = os.path.join(cfg.ENCRYPTED_MOUNTPOINT, "current")
  Shell("echo '%s' | encfs -S %s %s" % (cfg.ENCFS_PASSWORD,
                                        current,
                                        cfg.DECRYPTED_MOUNTPOINT))

  # Rsync files over to the decrypted mount point
  for filepath in cfg.FILEPATHS:
    Shell("rsync %s %s %s" % (filepath,
                              cfg.RSYNC_FLAGS,
                              cfg.DECRYPTED_MOUNTPOINT))

  # Optionally unmount filesystem
  should_unmount = True
  try:
    should_unmount = (cfg.UNMOUNT_AT_END == "True")
  except AttributeError:  # UNMOUNT_AT_END not defined defaults to True
    pass
  if should_unmount:
    Shell("fusermount -u %s" % cfg.DECRYPTED_MOUNTPOINT)

def MakeSnapshot(cfg, frequency):
  total_snapshots = 0
  for pair in cfg.SNAPSHOTS.split(','):
    key, value = pair.split('=')
    if key == frequency:
      total_snapshots = int(value)

  assert total_snapshots > 0, ("%s snapshot requested, 0 copies in .backuprc" %
                               frequency_type)

  # e.g. rm -rf backup.3
  backup_to_delete = os.path.join(cfg.ENCRYPTED_MOUNTPOINT,
                                  "%s.%s" % (frequency, total_snapshots))
  Shell("rm -rf %s" % backup_to_delete)

  # e.g. mv backup.2 backup.3; mv backup.1 backup.2
  for ii in range(total_snapshots, 1, -1):
    source_backup = os.path.join(cfg.ENCRYPTED_MOUNTPOINT,
                                 "%s.%s" % (frequency, ii - 1))
    dest_backup = os.path.join(cfg.ENCRYPTED_MOUNTPOINT,
                               "%s.%s" % (frequency, ii))
    if os.path.exists(source_backup):
      Shell("mv %s %s" % (source_backup, dest_backup))

  # eg cp -al current backup.1
  current = os.path.join(cfg.ENCRYPTED_MOUNTPOINT, "current")
  dest_backup = os.path.join(cfg.ENCRYPTED_MOUNTPOINT, "%s.1" % frequency)
  Shell("cp -al %s %s" % (current, dest_backup))

cfg = BackupConfig(BACKUP_RC)
logging.basicConfig(filename=cfg.LOG_FILE, level=logging.DEBUG,
                    format="%(asctime)s - %(levelname)s - %(message)s")


if 'snapshot' in sys.argv[1:]:
  try:
    logging.info("Start Backup")
    RunBackup(cfg)
  except Exception as e:
    logging.exception("Unhandled exception")
    raise
  finally:
    logging.info("End Backup")

for arg in sys.argv[1:]:
  if arg != 'snapshot':
    try:
      logging.info("Start %s Snapshot" % arg)
      MakeSnapshot(cfg, arg)
    except Exception as e:
      logging.exception("Unhandled exception")
      raise
    finally:
      logging.info("End %s Snapshot" % arg)
