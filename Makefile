CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Wpedantic

.PHONY: all demo web test clean
all: peaje

peaje: src/main.cpp
	$(CXX) $(CXXFLAGS) $< -o $@ -pthread

demo: peaje
	./peaje --modo demo

web: peaje
	python3 web/server.py

test: peaje
	python3 -m unittest discover -s tests -v

clean:
	rm -f peaje
