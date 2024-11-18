import os
import sys
from io import StringIO
from shutil import which
from pathlib import Path

from conan import ConanFile
from conan.errors import ConanException
from conan.tools.files import copy, save, load
from conan.tools.scm import Version
from conan.tools.env import VirtualRunEnv
import subprocess


class VirtualPythonEnv:
    def __init__(self, conanfile: ConanFile):
        self.conanfile: ConanFile = conanfile

    def generate(self) -> None:
        output_folder = "venv"
        bin_venv_path = "Scripts" if self.conanfile.settings.os == "Windows" else "bin"

        # Check if CPython is added as a dependency use the Conan recipe if available; if not use system interpreter
        try:
            cpython = self.conanfile.dependencies["cpython"]
            py_interp = cpython.conf_info.get("user.cpython:python").replace("\\", "/")
        except KeyError:
            py_interp = sys.executable

        run_env = VirtualRunEnv(self.conanfile)
        env = run_env.environment()
        env_vars = env.vars(self.conanfile, scope="run")

        self.conanfile.output.info(f"Using Python interpreter '{py_interp}' to create Virtual Environment in '{output_folder}'")
        with env_vars.apply():
            subprocess.run([py_interp, "-m", "venv", "--copies", output_folder])

        # Make sure there executable is named the same on all three OSes this allows it to be called with `python`
        # simplifying GH Actions steps
        if self.conanfile.settings.os != "Windows":
            py_interp_venv = Path(output_folder, bin_venv_path, "python")
            if not py_interp_venv.exists():
                py_interp_venv.hardlink_to(
                    Path(output_folder, bin_venv_path, Path(sys.executable).stem + Path(sys.executable).suffix))
        else:
            py_interp_venv = Path(output_folder, bin_venv_path,
                                Path(sys.executable).stem + Path(sys.executable).suffix)

        with env_vars.apply():
            buffer = subprocess.run([py_interp_venv, "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], capture_output=True, encoding="utf-8").stdout
        pythonpath = buffer.splitlines()[-1]

        env.define_path("VIRTUAL_ENV", output_folder)
        env.prepend_path("PATH", os.path.join(output_folder, bin_venv_path))
        env.prepend_path("LD_LIBRARY_PATH", os.path.join(output_folder, bin_venv_path))
        env.prepend_path("DYLD_LIBRARY_PATH", os.path.join(output_folder, bin_venv_path))
        env.prepend_path("PYTHONPATH", pythonpath)
        env.unset("PYTHONHOME")

        filepath = str(Path(self.conanfile.generators_folder).joinpath("supercoucou_runenv"))
        env_vars.save_script(filepath)

        # Install some base_packages
        with env_vars.apply():
            subprocess.run([py_interp_venv, "-m", "pip", "install", "wheel", "setuptools"])

        if self.conanfile.settings.os != "Windows":
            content = f"source {os.path.join(output_folder, 'conan', 'virtual_python_env.sh')}\n" + load(self.conanfile,
                                                                                                        os.path.join(
                                                                                                            output_folder,
                                                                                                            bin_venv_path,
                                                                                                            "activate"))
            save(self.conanfile, os.path.join(output_folder, bin_venv_path, "activate"), content)

        pip_requirements = {}
        self._populate_pip_requirements(self.conanfile, "pip_requirements", pip_requirements, str(self.conanfile.settings.os))

        requirements_hashed_txt = []
        requirements_url_txt = []
        for name, req in pip_requirements.items():
            if "url" in req:
                requirements_url_txt.append(req['url'])
            else:
                requirement_txt = [f"{name}=={req['version']}"]

                if "hashes" in req:
                    for hash_str in req['hashes']:
                        requirement_txt.append(f"--hash={hash_str}")

                requirements_hashed_txt.append(" ".join(requirement_txt))

        self._install_pip_requirements("hashed", requirements_hashed_txt, output_folder, env_vars, py_interp_venv)
        self._install_pip_requirements("url", requirements_url_txt, output_folder, env_vars, py_interp_venv)

        if self.conanfile.conf.get("user.generator.virtual_python_env:dev_tools", default = False, check_type = bool):
            pip_requirements_dev = []
            self._populate_pip_requirements_dev(self.conanfile, pip_requirements_dev)
            print(pip_requirements_dev)
            self._install_pip_requirements("dev", pip_requirements_dev, output_folder, env_vars, py_interp_venv)


    def _populate_pip_requirements_dev(self, conanfile, pip_requirements_dev, add_dependencies = True):
        if hasattr(conanfile, "conan_data") and "pip_requirements_dev" in conanfile.conan_data:
            print(conanfile.conan_data["pip_requirements_dev"])
            pip_requirements_dev += conanfile.conan_data["pip_requirements_dev"]

        if add_dependencies:
            for name, dep in reversed(self.conanfile.dependencies.host.items()):
                self._populate_pip_requirements_dev(dep, pip_requirements_dev, add_dependencies = False)


    def _populate_pip_requirements(self, conanfile, key, pip_requirements, actual_os, add_dependencies = True):
        if hasattr(conanfile, "conan_data") and key in conanfile.conan_data:
            for system in (system for system in conanfile.conan_data[key] if system in ("any", actual_os)):
                for name, req in conanfile.conan_data[key][system].items():
                    if name not in pip_requirements or Version(pip_requirements[name]["version"]) < Version(req["version"]):
                        pip_requirements[name] = req

        if add_dependencies:
            for name, dep in reversed(self.conanfile.dependencies.host.items()):
                self._populate_pip_requirements(dep, key, pip_requirements, actual_os, add_dependencies = False)


    def _install_pip_requirements(self, file_suffix, file_content, output_folder, env_vars, py_interp_venv):
        if len(file_content) > 0:
            pip_file_path = os.path.join(output_folder, 'conan', f'requirements_{file_suffix}.txt')
            save(self.conanfile, pip_file_path, "\n".join(file_content))
            with env_vars.apply():
                subprocess.run([py_interp_venv, "-m", "pip", "install", "-r", pip_file_path])
