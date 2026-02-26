# visualisations.py
import matplotlib.pyplot as plt
import pandas as pd


def plot_navs(nav_dict: dict[str, pd.Series], title: str = "NAV Performance") -> None:
    plt.figure()
    for name, nav in nav_dict.items():
        plt.plot(nav.index, nav.values, label=name)
    plt.title(title)
    plt.xlabel("Date")
    plt.ylabel("NAV")
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.show()