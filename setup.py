from setuptools import setup, find_packages

setup(
    name='whisper-movie-subtitles',
    version='1.0',
#    packages=find_packages(),
    py_modules=['whisper-movie-subtitles'],    
    entry_points={
        'console_scripts': ['whisper-movie-subtitles=whisper-movie-subtitles:main']
    },
    install_requires=[
        'argparse',
        'moviepy',
    ],
)
