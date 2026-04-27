from setuptools import setup, find_packages

setup(
    name="debugflow",
    version="0.1.0",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    entry_points={
        'console_scripts': [
            'flow=debugflow.flow_service:main',
        ],
    },
)