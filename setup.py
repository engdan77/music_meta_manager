# from setuptools import setup
from setuptools import setup
with open("requirements.txt") as f:
    dependencies = [line for line in f]

setup(
    name='music_meta_manager',
    version='0.0.3',
    packages=['musicmanager'],
    url='',
    license='',
    author='edo',
    author_email='daniel@engvalls.eu',
    description='Tool for managing meta music information',
    install_requires=dependencies,
    entry_points={
        'console_scripts': ['music_migrate=musicmanager.cli:cli_migrate', 'music_fix_location=musicmanager.cli:cli_fix_location'],
    }
)
