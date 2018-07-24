from setuptools import setup, find_packages


setup(
    name='sentinel',
    version='1.0',
    description='Python project to register container docker as service in a backend',
    author='Ophélie Mauger',
    author_email='ophelie.mauger@alterway.fr',
    entry_points={
        'console_scripts': ['sentinel=sentinel.sentinel:main']
    },
    package_dir={'': 'sentinel'},
    packages=find_packages(where="sentinel"),
    install_requires=[
        "docker==3.0.*",
        "requests==2.18.*",
        "jinja2==2.10.*",
        "zope.interface==4.5.*",
        'dependencies-injection==1.0'
    ],
    extras_require={
        'ci': ['mock', 'flake8', 'coverage', 'pylint', 'radon', 'xenon'],
    }
)
