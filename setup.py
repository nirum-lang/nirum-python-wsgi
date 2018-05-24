import ast
import os
import re
import sys

from setuptools import setup, __version__ as setuptools_version


def readme(name='README.rst'):
    try:
        with open(name) as f:
            rst = f.read()
        return re.sub(
            r'(^|\n).. include::\s*([^\n]+)($|\n)',
            lambda m: m.group(1) + (readme(m.group(2)) or '') + m.group(3),
            rst
        )
    except (IOError, OSError):
        return


def get_version():
    module_path = os.path.join(os.path.dirname(__file__), 'nirum_wsgi.py')
    module_file = open(module_path, 'rb')
    try:
        module_code = module_file.read()
    finally:
        module_file.close()
    tree = ast.parse(module_code, module_path)
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, = node.targets
        if isinstance(target, ast.Name) and target.id == '__version__':
            value = node.value
            if isinstance(value, ast.Str):
                return value.s
            raise ValueError('__version__ is not defined as a string literal')
    raise ValueError('could not find __version__')


setup_requires = []
install_requires = [
    'nirum >= 0.6.3',
    'six',
    'Werkzeug >= 0.11, < 1.0',
]
tests_require = [
    'flake8-import-order >= 0.17.1, < 1.0',
    'flake8-import-order-spoqa >= 1.4.0, < 2.0.0',
    'pycodestyle < 2.4.0',  # FIXME: remove pinning when new flake8 is released
    'pytest >= 3.5.1, < 4.0.0',
    'pytest-flake8 >= 1.0.1, < 1.1.0',
    'requests-mock >= 1.5.0, < 1.6.0',
]
extras_require = {
    'tests': tests_require,
}
below35_requires = [
    'typing',
]


if 'bdist_wheel' not in sys.argv and sys.version_info < (3, 5):
    install_requires.extend(below35_requires)


if tuple(map(int, setuptools_version.split('.'))) < (17, 1):
    setup_requires = ['setuptools >= 17.1']
    extras_require.update({":python_version=='3.4'": below35_requires})
    extras_require.update({":python_version=='2.7'": below35_requires})
else:
    extras_require.update({":python_version<'3.5'": below35_requires})


setup(
    name='nirum-wsgi',
    version=get_version(),
    description='Nirum services as WSGI apps',
    long_description=readme(),
    url='https://github.com/spoqa/nirum-python-wsgi',
    bugtrack_url='https://github.com/spoqa/nirum/issues',
    author='Nirum team',
    license='MIT license',
    py_modules=['nirum_wsgi'],
    install_requires=install_requires,
    setup_requires=setup_requires,
    extras_require=extras_require,
    entry_points={
        'console_scripts': [
            'nirum-server = nirum_wsgi:main',
        ],
    },
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Topic :: Internet :: WWW/HTTP :: WSGI :: Application',
        'Topic :: Software Development :: Code Generators',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: Software Development :: Object Brokering',
    ]
)
