import sublime, sublime_plugin
import os
import re
import subprocess
import Default
from distutils import spawn


PLATFORM = sublime.platform()

class Settings(object):
    @staticmethod
    def get(key):
        os.environ["PackageDir"] = os.path.join(sublime.packages_path(), "OpenSees")
        settings = sublime.load_settings("OpenSees.sublime-settings")
        project_data = sublime.active_window().project_data()
        keys = key.split(".")
        k = keys[0]
        if project_data is not None and k in project_data:
            setting = project_data[k]
        else:
            setting = settings.get(k)
        for k in keys[1:]:
            setting = setting[k]
        return Settings._replace_references(setting)
    @staticmethod
    def _replace_references(setting):
        r_lambda = lambda token: re.compile(r"\${" + token + "}")
        if isinstance(setting, dict):
            if PLATFORM in setting:
                return Settings._replace_references(setting[PLATFORM])
            for k, v in setting.items():
                setting[k] = Settings._replace_references(v)
        if isinstance(setting, list):
            for i, v in enumerate(setting):
                setting[i] = Settings._replace_references(v)
        if isinstance(setting, str):
            setting = os.path.expandvars(setting)
            for match in r_lambda(r"[^}]*").findall(setting):
                token = match[2:-1]
                setting = r_lambda(token).sub(str(Settings.get(token)).replace("\\", "\\\\"), setting)
        return setting

def norm_path(path):
    return os.path.normpath(os.path.normcase(path))

def save_all_views(window, path):
    for view in window.views():
        fname = view.file_name()
        if (fname and view.is_dirty() and os.path.exists(fname) and norm_path(fname).startswith(norm_path(path))):
            view.run_command("save")

def which(path):
    return spawn.find_executable(path)

def cpu_count():
    # Linux, Unix and MacOS:
    if hasattr(os, "sysconf"):
        if os.sysconf_names.has_key("SC_NPROCESSORS_ONLN"):
            ncpus = os.sysconf("SC_NPROCESSORS_ONLN")
            if isinstance(ncpus, int) and ncpus > 0:
                return ncpus
        else:
            return int(os.popen2("sysctl -n hw.ncpu")[1].read())
    # Windows:
    if "NUMBER_OF_PROCESSORS" in os.environ:
        ncpus = int(os.environ["NUMBER_OF_PROCESSORS"]);
        if ncpus > 0:
            return ncpus
    return 1 # Default

class OnDoneExecCommand(Default.exec.ExecCommand):
    # overriden from ExecCommand
    def __init__(self, window, display_name=None, on_done=None, stdout=None):
        super().__init__(window)
        self.display_name = display_name
        self.on_done = on_done
        self.stdout = stdout
    # overriden from ExecCommand
    def run(self, **kwargs):
        super().run(**kwargs)
        self.append_string(self.proc, "[%s Started]\n\n" % self.display_name)
    # overriden from ExecCommand
    def append_string(self, proc, str):
        if not proc.poll() and self.display_name:
            str = "\n\n" + re.sub(r"(Finished)", self.display_name + r" \1", str, 1)
        super().append_string(proc, str)
        if isinstance(self.stdout, list):
            self.stdout.append(str)
    # overriden from ExecCommand
    def on_finished(self, proc):
        self.run_callbacks()
        super().on_finished(proc)
    # custom method
    def popen(self):
        return self.proc.proc
    # custom method
    def run_message(self, message):
        comment = "::" if PLATFORM == "windows" else "#"
        self.run(shell_cmd = "%s [%s] %s" % (comment, self.display_name, message))
        self.append_string(self.proc, message)
    # custom method
    def run_callbacks(self):
        if not isinstance(self.on_done, list):
            self.on_done = [self.on_done]
        for od in self.on_done:
            if od is not None:
                od()

class RunBase(sublime_plugin.WindowCommand):
    def run(self, paths = []):
        name = "OpenSees " + self.get_name()
        path = self.get_path(paths)
        basename = os.path.basename(path)
        command = OnDoneExecCommand(self.window, "RUN %s for \"%s\"" % (name, basename))
        if path is None:
            command.run_message("Input file \"%s\" is not a valid %s file." % (path, name))
            return None
        save_all_views(self.window, path)
        cmd = self.get_cmd(name, basename, command)
        if cmd is None:
            return None
        command.run(
            shell_cmd = cmd,
            file_regex = r"^\s*\(file \"([^\"]+)\" line (\d+)\)$",
            working_dir = os.path.dirname(path)
        )
        return command.popen()
    def is_enabled(self, paths = []):
        return self.get_path(paths) is not None
    def is_visible(self, paths = []):
        return self.is_enabled(paths)
    def get_path(self, paths):
        path = None
        if len(paths) < 1:
            path = self.window.active_view().file_name()
        elif len(paths) == 1:
            path = paths[0]
        if (path is None or not os.path.exists(path)):
            return None
        return path
    def get_cmd(self, name, basename, command):
        executable = Settings.get(self.get_exe_setting_name())
        if which(executable) is None:
            command.run_message("%s executable \"%s\" was not found, make sure it is installed." % (name, executable))
            return None
        cmd = "\"%s\" \"%s\"" % (executable, basename)
        if self.is_parallel():
            mpiexec = os.path.normpath(Settings.get("mpiexec"))
            if which(mpiexec) is None:
                command.run_message("MPI executable for %s \"%s\" was not found, make sure it is installed." % (name, mpiexec))
                return None
            max_processor_count = cpu_count()
            try:
                processor_count = int(Settings.get("processor_count"))
                if not 0 < x <= max_processor_count:
                    raise Exception("Processor count not in valid range")
            except Exception:
                processor_count = max_processor_count
            if PLATFORM == "windows":
                args = "-noprompt"
                smpd = os.path.join(os.path.dirname(mpiexec), "smpd")
                try:
                    if not subprocess.check_output("\"%s\" -status" % smpd, shell=True).startswith(b"smpd running"):
                        raise subprocess.CalledProcessError(-1, "smpd -status")
                    is_mpiexec_valid = lambda: subprocess.check_output("\"%s\" -validate" % mpiexec, shell=True).startswith(b"SUCCESS")
                    if not is_mpiexec_valid():
                        messages = [
                            "MPICH2 needs a username and password (same account as windows)",
                            "Running command:",
                            "    \"%s\" -register" % mpiexec
                        ]
                        subprocess.call("start /wait cmd /c \"echo %s & echo. & \"%s\" -register\"" % (" & echo ".join(messages), mpiexec), shell=True)
                        if not is_mpiexec_valid():
                            command.run_message("MPICH2 account registration unsuccessful, need to run script again or the following from a command prompt:\n\t\"%s\" -register" % mpiexec)
                            return None
                except subprocess.CalledProcessError:
                    command.run_message("MPICH2 service \"smpd\" is not running, need to run the following from an administrator command prompt:\n\t\"%s\" -start" % smpd)
                    return None
            else:
                #TODO: test linux and mac os x and see if there is anything special todo
                args = ""
            cmd = "\"%s\" %s -np %s " % (mpiexec, args, processor_count) + cmd
        return cmd
    def get_name(self):
        raise NotImplementedError("Should have implemented \"get_name(self)\" method.")
    def is_parallel(self):
        raise NotImplementedError("Should have implemented \"is_parallel(self)\" method.")
    def get_exe_setting_name(self):
        raise NotImplementedError("Should have implemented \"get_exe_setting_name(self)\" method.")