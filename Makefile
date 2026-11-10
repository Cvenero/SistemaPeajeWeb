CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Wpedantic

.PHONY: all demo test clean
all: peaje

peaje: src/main.cpp
	$(CXX) $(CXXFLAGS) $< -o $@ -pthread

demo: peaje
	./peaje --modo demo

test: peaje
	python3 tests/test_simulador.py

clean:
	rm -f peaje
