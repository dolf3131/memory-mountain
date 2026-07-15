# Portable memory-mountain build. Works with g++, clang++, or MSVC clang-cl via CXX=.
CXX ?= $(shell command -v g++ 2>/dev/null || command -v clang++ 2>/dev/null || echo c++)
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -pedantic
# Metal host must use Apple clang (frameworks).
CLANGXX ?= $(shell xcrun --find clang++ 2>/dev/null || command -v clang++ 2>/dev/null || echo clang++)
# Prefer miniforge/conda Python (has matplotlib) over system /usr/bin/python3.
PYTHON ?= $(firstword \
	$(wildcard $(HOME)/miniforge3/bin/python) \
	$(wildcard $(HOME)/mambaforge/bin/python) \
	$(wildcard $(HOME)/miniconda3/bin/python) \
	$(wildcard $(HOME)/anaconda3/bin/python) \
	$(shell command -v python3 2>/dev/null))
TARGET = mountain
TARGET_METAL = mountain_metal

.PHONY: all host run plot all-run metal metal-run metal-plot metal-all clean

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

# --- Metal GPU (macOS / Apple Silicon only) ---
metal: $(TARGET_METAL)

$(TARGET_METAL): mountain_metal.mm mountain.metal
	@if [ "$$(uname -s)" != "Darwin" ]; then echo "Metal target requires macOS"; exit 1; fi
	SDKROOT=$$(xcrun --sdk macosx --show-sdk-path); \
	$(CLANGXX) -std=c++17 -fobjc-arc -O2 -Wall -Wextra \
		-isysroot $$SDKROOT \
		mountain_metal.mm -o $(TARGET_METAL) \
		-framework Metal -framework Foundation

metal-run: metal
	mkdir -p output
	./$(TARGET_METAL) output/mountain_metal.csv

metal-plot:
	$(PYTHON) plot_mountain.py \
		--csv output/mountain_metal.csv \
		--host output/host_info_metal.json \
		--out output/memory_mountain_metal.png

metal-all: metal-run metal-plot

clean:
	rm -f $(TARGET) $(TARGET).exe $(TARGET_METAL)
