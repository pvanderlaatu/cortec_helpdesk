from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = f.read().strip().split("\n")

setup(
    name="cortec_helpdesk",
    version="1.0.1",
    description="Customizaciones de Frappe Helpdesk para CORTEC",
    author="Corporación de Tecnología CORTEC S.R.L.",
    author_email="soporte@tecnocr.net",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires,
    license="AGPL-3.0",
)
