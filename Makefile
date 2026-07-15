# Portable memory-mountain build. Works with g++, clang++, or MSVC clang-cl via CXX=.
CXX ?= $(shell command -v g++ 2>/dev/null || command -v clang++ 2>/dev/null || echo c++)
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -pedantic
# Prefer miniforge/conda Python (has matplotlib) over system /usr/bin/python3.
PYTHON ?= $(firstword \
	$(wildcard $(HOME)/miniforge3/bin/python) \
	$(wildcard $(HOME)/mambaforge/bin/python) \
	$(wildcard $(HOME)/miniconda3/bin/python) \
	$(wildcard $(HOME)/anaconda3/bin/python) \
	$(shell command -v python3 2>/dev/null))
TARGET = mountain

.PHONY: all host run plot all-run clean

all: $(TARGET)

$(TARGET): mountain.cpp
	$(CXX) $(CXXFLAGS) mountain.cpp -o $(TARGET)

host:
	$(PYTHON) detect_host.py

run: $(TARGET)
	mkdir -p output
	./$(TARGET) output/mountain.csv

plot: host
	$(PYTHON) plot_mountain.py

# One-shot: build → measure → detect host → plot
all-run: all run plot

clean:
	rm -f $(TARGET) $(TARGET).exe
