# Portable memory-mountain build. Works with g++, clang++, or MSVC clang-cl via CXX=.
CXX ?= $(shell command -v g++ 2>/dev/null || command -v clang++ 2>/dev/null || echo c++)
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -pedantic
TARGET = mountain

.PHONY: all host run plot all-run clean

all: $(TARGET)

$(TARGET): mountain.cpp
	$(CXX) $(CXXFLAGS) mountain.cpp -o $(TARGET)

host:
	python3 detect_host.py

run: $(TARGET)
	mkdir -p output
	./$(TARGET) output/mountain.csv

plot: host
	python3 plot_mountain.py

# One-shot: build → measure → detect host → plot
all-run: all run plot

clean:
	rm -f $(TARGET) $(TARGET).exe
